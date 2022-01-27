#!/usr/bin/env bash

restic backup /var/lib/rancher/k3s
restic forget --keep-within-daily 7d --keep-within-weekly 1m --keep-within-monthly 1y
