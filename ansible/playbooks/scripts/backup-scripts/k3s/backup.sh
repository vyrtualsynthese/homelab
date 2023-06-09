#!/usr/bin/env bash

crictl rmi --prune
sqlite3 /var/lib/rancher/k3s/server/db/state.db .dump > /var/lib/rancher/k3s/server/db/state.db.backup
restic backup /var/lib/rancher/k3s/server/db/state.db.backup /var/lib/rancher/k3s/server/token
restic forget  --keep-within-daily 7d --keep-within-weekly 1m --prune
rm /var/lib/rancher/k3s/server/db/state.db.backup
