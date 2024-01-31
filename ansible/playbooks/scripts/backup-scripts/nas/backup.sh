#!/usr/bin/env bash

crictl rmi --prune
restic backup /mnt/storage/minio/.minio.sys -v
restic forget  --keep-within-daily 7d --keep-within-weekly 1m --prune -v
