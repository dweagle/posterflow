#!/bin/bash
set -e

PUID=${PUID:-1000}
PGID=${PGID:-1000}
UMASK=${UMASK:-022}

# Update group and user to match host PUID/PGID
CURRENT_GID=$(getent group posterflow | cut -d: -f3)
CURRENT_UID=$(id -u posterflow)

if [ "$CURRENT_GID" != "$PGID" ]; then
	groupmod -o -g "$PGID" posterflow
fi

if [ "$CURRENT_UID" != "$PUID" ]; then
	usermod -o -u "$PUID" posterflow
fi

chown posterflow:posterflow /config

# Inherited through gosu by the app and every rclone subprocess
umask "$UMASK"

TIMESTAMP=$(date +"%y/%m/%d %H:%M:%S")
printf "%s | INFO     | [    STARTUP    ] • Entrypoint ready (uid=%s gid=%s umask=%s)\n" "$TIMESTAMP" "$PUID" "$PGID" "$UMASK"

# Drop to posterflow user and exec the command
exec gosu posterflow "$@"
