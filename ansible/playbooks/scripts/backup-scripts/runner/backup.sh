#!/usr/bin/env bash

docker system prune -af
restic backup /home/vronteix/dockers --exclude="gravity_old.db" --exclude="*.bak"
restic forget --keep-within-daily 7d --keep-within-weekly 1m --prune
