#!/usr/bin/env python3
"""Check the cluster and the manifests for APIs that Kubernetes 1.32-1.36 stop serving.

Run from the repository root, with kubectl pointed at the cluster:

    python3 hack/check-api-removals.py

Reads three things and nothing else:

  * `kubectl get --raw /apis` and `/api` — every group/version this apiserver serves;
  * `kubectl get pv -o json` — the live objects whose schema the climb changes;
  * every `*.yaml` / `*.yml` under `cluster/` and `kubectl/` — what Argo CD re-applies.

A removed group/version is a hit only when no other version of the same group is served:
then its objects cannot be read after the climb. A version that is served today because
the cluster has not reached the release that drops it, while the group keeps a newer
version, is printed as a `note:` — its objects travel through the surviving version, and
only a client hard-coded to the old one would break.

Exit: 0 clean, 1 hits, 2 kubectl could not be read.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST_ROOTS = ("cluster", "kubectl")

# apiVersion -> (release that stops serving it, what to migrate to).
# Sources, read on 2026-11-23:
#   https://kubernetes.io/docs/reference/using-api/deprecation-guide/   (1.32)
#   https://github.com/kubernetes/kubernetes/blob/master/CHANGELOG/CHANGELOG-1.35.md
#   https://github.com/kubernetes/kubernetes/blob/master/CHANGELOG/CHANGELOG-1.36.md
# 1.33 and 1.34 removed no group/version: the guide has no section for them and their
# changelogs name none.
REMOVED = {
    "flowcontrol.apiserver.k8s.io/v1beta3": ("1.32", "flowcontrol.apiserver.k8s.io/v1"),
    "storage.k8s.io/v1alpha1": ("1.35", "storage.k8s.io/v1"),
    "storagemigration.k8s.io/v1alpha1": ("1.35", "storagemigration.k8s.io/v1beta1"),
    "groupsnapshot.storage.k8s.io/v1alpha1": ("1.36", "groupsnapshot.storage.k8s.io/v1beta1"),
}

# kind -> (release, field path, reason). A removed API field survives on the object it
# was written into, which is why the live objects are read and not only the versions.
REMOVED_FIELDS = {
    "PersistentVolume": (
        "1.36",
        "spec.portworxVolume",
        "the in-tree Portworx plugin; use the pxd.portworx.com CSI driver",
    ),
}

API_VERSION_RE = re.compile(r"^[ \t-]*apiVersion:[ \t]*[\"']?([^\"'\s#]+)", re.MULTILINE)


def _group(api_version):
    return api_version.rsplit("/", 1)[0] if "/" in api_version else ""


def _survivor(api_version, served):
    """A served version of the same group other than this one, or None."""
    group = _group(api_version)
    for other in sorted(served):
        if other != api_version and _group(other) == group:
            return other
    return None


def check_served(served):
    """Split the served group/versions into `hits` and `notes`.

    `served` is the set of `group/version` strings the apiserver answers on, core as `v1`.
    """
    hits, notes = [], []
    for api_version in sorted(served):
        if api_version not in REMOVED:
            continue
        release = REMOVED[api_version][0]
        survivor = _survivor(api_version, served)
        if survivor:
            notes.append(
                f"{api_version} is served and {release} stops serving it; "
                f"{survivor} carries the same objects"
            )
        else:
            hits.append(f"{api_version}: {release} stops serving it and no version of its group takes over")
    return hits, notes


def check_object(obj):
    """Return one message per removed field present on a live object."""
    hits = []
    kind = obj.get("kind", "object")
    name = (obj.get("metadata") or {}).get("name", "-")
    for expected_kind, (release, path, reason) in sorted(REMOVED_FIELDS.items()):
        if kind != expected_kind:
            continue
        node = obj
        for key in path.split("."):
            node = node.get(key) if isinstance(node, dict) else None
        if node is not None:
            hits.append(f"{kind} {name}: {path} is removed in {release} — {reason}")
    return hits


def check_manifest_text(path, text):
    """Return one message per removed apiVersion a manifest declares."""
    hits = []
    for match in API_VERSION_RE.finditer(text):
        api_version = match.group(1)
        if api_version in REMOVED:
            line = text.count("\n", 0, match.start()) + 1
            hits.append(f"{path}:{line}: apiVersion {api_version} stops being served in {REMOVED[api_version][0]}")
    return hits


def manifest_files(root=ROOT):
    for top in MANIFEST_ROOTS:
        for dirpath, dirnames, filenames in os.walk(os.path.join(root, top)):
            dirnames.sort()
            for name in sorted(filenames):
                if name.endswith((".yaml", ".yml")):
                    yield os.path.join(dirpath, name)


def check_manifests(root=ROOT):
    hits = []
    for path in manifest_files(root):
        with open(path, encoding="utf-8", errors="replace") as handle:
            hits.extend(check_manifest_text(os.path.relpath(path, root), handle.read()))
    return hits


def _kubectl_json(*args):
    result = subprocess.run(
        ["kubectl", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def served_versions():
    """Every group/version the apiserver answers on, from the discovery documents."""
    served = set(_kubectl_json("get", "--raw", "/api").get("versions") or [])
    for group in _kubectl_json("get", "--raw", "/apis").get("groups") or []:
        for version in group.get("versions") or []:
            served.add(version.get("groupVersion"))
    return {item for item in served if item}


def main(argv):
    if argv and argv[0] in ("-h", "--help"):
        print(__doc__.strip())
        return 0
    try:
        served = served_versions()
        objects = _kubectl_json("get", "pv", "-o", "json").get("items") or []
    except (OSError, subprocess.CalledProcessError, ValueError) as error:
        print(f"kubectl could not be read: {error}", file=sys.stderr)
        return 2

    hits, notes = check_served(served)
    for obj in objects:
        hits.extend(check_object(obj))
    hits.extend(check_manifests())

    for note in notes:
        print(f"note: {note}")
    for hit in hits:
        print(f"HIT: {hit}")
    if hits:
        print(f"{len(hits)} hit(s): resolve each before the climb")
        return 1
    print("0 hits: no object and no manifest here sits on an API that 1.32-1.36 removes")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
