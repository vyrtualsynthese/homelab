#!/usr/bin/env bash

crictl rmi --prune
restic backup /var/lib/rancher/k3s
restic forget --keep-within-daily 7d --keep-within-weekly 1m --prune
