"""The embedded outpost must only name providers that exist, and blueprints that exist.

`cluster/helm/authentik/files/outpost-embedded.yaml` sets the embedded outpost's provider
list whole, so every `!Find` in it has to resolve. When one does not, authentik fails the
whole blueprint and the outpost silently keeps its previous list: every host it no longer
names answers 404, and nothing else in the cluster notices.

That happened twice. `docs` was missing until 2026-11-17. `filebrowser` was left in the
list after its proxy provider was replaced by an OIDC one on 2026-11-19; the file then
failed on every apply, which is why `syncthing` could not be attached on 2026-11-19 —
the new provider was created and never served.

This test reads the blueprints in git, so it needs no cluster.
"""

import re
from pathlib import Path

import pytest

FILES = Path(__file__).resolve().parents[2] / "cluster" / "helm" / "authentik" / "files"
OUTPOST = FILES / "outpost-embedded.yaml"

PROXY_MODEL = "authentik_providers_proxy.proxyprovider"
METAAPPLY_MODEL = "authentik_blueprints.metaapplyblueprint"

PROXY_REF = re.compile(
    r"!Find\s*\[\s*" + re.escape(PROXY_MODEL) + r"\s*,\s*\[\s*name\s*,\s*([^\]]+?)\s*\]\s*\]"
)
BLUEPRINT_NAME = re.compile(r"^metadata:\n\s+name:\s*(\S+)\s*$", re.MULTILINE)


def _entries(text):
    """Split a blueprint into its `- model: ...` entries."""
    parts = re.split(r"^\s*-\s+model:\s*", text, flags=re.MULTILINE)[1:]
    return [(part.split("\n", 1)[0].strip(), part.split("\n", 1)[1] if "\n" in part else "") for part in parts]


def _first_name(body):
    match = re.search(r"^\s+name:\s*(\S+)\s*$", body, re.MULTILINE)
    return match.group(1) if match else None


def proxy_providers():
    names = set()
    for path in sorted(FILES.glob("*.yaml")):
        for model, body in _entries(path.read_text()):
            if model == PROXY_MODEL:
                name = _first_name(body)
                if name:
                    names.add(name)
    return names


def blueprints():
    names = set()
    for path in sorted(FILES.glob("*.yaml")):
        match = BLUEPRINT_NAME.search(path.read_text())
        if match:
            names.add(match.group(1))
    return names


def outpost_proxy_refs():
    return PROXY_REF.findall(OUTPOST.read_text())


def outpost_metaapply_refs():
    refs = []
    for model, body in _entries(OUTPOST.read_text()):
        if model == METAAPPLY_MODEL:
            name = _first_name(body)
            if name:
                refs.append(name)
    return refs


def test_outpost_only_names_existing_proxy_providers():
    declared = proxy_providers()
    missing = sorted(set(outpost_proxy_refs()) - declared)
    assert missing == [], (
        f"outpost-embedded.yaml names proxy providers no blueprint declares: {missing}. "
        "authentik fails the whole blueprint when a !Find does not resolve, and the outpost "
        "then keeps its previous provider list."
    )


def test_outpost_only_metaapplies_existing_blueprints():
    declared = blueprints()
    missing = sorted(set(outpost_metaapply_refs()) - declared)
    assert missing == [], f"outpost-embedded.yaml metaapplies blueprints that do not exist: {missing}"


@pytest.mark.xfail(
    strict=True,
    reason="app-vikunja.yaml still declares the retired forward-auth provider; card #352 owns the cleanup",
)
def test_every_declared_proxy_provider_is_on_the_outpost():
    """A provider that exists but is not listed is created and ignored: its host answers 404.

    Known drift, so expected to fail today: `app-vikunja.yaml` still declares the
    forward-auth provider that was retired when Vikunja moved to OIDC on 2026-11-16,
    and the outpost no longer lists it. The dead entry is a landmine rather than a
    live fault — the provider itself is gone — but the next apply of that blueprint
    would recreate it and point the Vikunja application at it. Card #352 owns it.
    """
    unlisted = sorted(proxy_providers() - set(outpost_proxy_refs()))
    assert unlisted == [], (
        f"proxy providers missing from outpost-embedded.yaml: {unlisted}. "
        "The outpost sets its provider list whole, so an unlisted provider serves nothing."
    )
