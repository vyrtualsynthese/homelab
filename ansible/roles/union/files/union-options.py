#!/usr/bin/env python3
"""Read, or converge, the runtime options of the mergerfs union.

mergerfs keeps its live configuration in xattrs on a virtual control file,
`<mount>/.mergerfs`, and that is the only runtime path it documents: `mount -o
remount` is handed to libfuse and rejected (`fuse: unknown option 'remount'`,
rehearsed on `nas` with mergerfs 2.33.3). So the fstab line and this script are
two halves of one change — the line for the next boot, the xattrs for the mount
that is running.

The expected values arrive as JSON in $UNION_RUNTIME, already in the form
mergerfs reports them: `moveonenospc=true` is an alias for the `mfs` policy, and
a size prints in bytes. Without `--apply` the script only reports and exits 1
when the mount differs; with it, it writes what differs, reads it back, and says
`changed` or `unchanged` on stdout — `unchanged` is what a converged host prints,
which is what keeps `make check` quiet.
"""

import json
import os
import sys

CONTROL = "/mnt/storage/.mergerfs"


def fail(message):
    print(message, file=sys.stderr)
    return 1


def main(argv):
    control = CONTROL
    if "--control" in argv:
        control = argv[argv.index("--control") + 1]
    apply = "--apply" in argv

    try:
        wanted = json.loads(os.environ["UNION_RUNTIME"])
    except KeyError:
        return fail("UNION_RUNTIME is not set; refusing to guess what the union should be")
    except ValueError as error:
        return fail("UNION_RUNTIME is not JSON: %s" % error)

    try:
        os.listxattr(control)
    except OSError as error:
        return fail(
            "cannot read %s: %s — the union is not mounted, so there is nothing to converge"
            % (control, error.strerror)
        )

    differs = []
    for name in sorted(wanted):
        key = "user.mergerfs." + name
        current = os.getxattr(control, key).decode()
        if not apply:
            print("%s=%s" % (name, current))
        if current == wanted[name]:
            continue
        differs.append(name)
        if not apply:
            continue
        os.setxattr(control, key, wanted[name].encode())
        if os.getxattr(control, key).decode() != wanted[name]:
            return fail("%s did not take: %s still reads %s" % (name, key, os.getxattr(control, key).decode()))

    if not apply:
        if differs:
            return fail("differs: %s" % ", ".join(sorted(differs)))
        return 0

    # One line, and only one: the `script` module merges the script's stderr into
    # its stdout, so a task that compares stdout with `changed` needs a line it
    # can match from the start — `changed: minfreespace` is still a change.
    print("changed: %s" % ", ".join(sorted(differs)) if differs else "unchanged")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
