#!/usr/bin/env bash

restic backup /home/vronteix
restic forget --keep-within-daily 7d --keep-within-weekly 1m --prune
