#!/bin/bash
# Written by ansible/pi/hosts-pi.sh; an edit here is reverted on the next setup.
#
# keepalived's `vrrp_script` runs its command as an argv, never through a shell,
# so a pipeline written there is handed to the binary as arguments and fails
# every time. This file is the shell: it answers one question, and a host whose
# panel is gone gives up the shared address instead of holding it with nothing
# behind it.
set -eu

# The same seam every script here carries. A fixture has no docker: it renders
# this file and reads it, and never runs it against a container.
if [ -n "${WG_ROOT:-}" ]; then
    exit 0
fi

docker inspect -f '{{.State.Running}}' wg-easy | grep -qx true
