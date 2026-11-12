#!/bin/bash
set -e

PUID=${PUID:-1000}
PGID=${PGID:-1000}
UMASK=${UMASK:-022}
EXTRA_GROUPS=${EXTRA_GROUPS:-}

# Update group and user to match host PUID/PGID
CURRENT_GID=$(getent group posterflow | cut -d: -f3)
CURRENT_UID=$(id -u posterflow)

if [ "$CURRENT_GID" != "$PGID" ]; then
	groupmod -o -g "$PGID" posterflow
fi

if [ "$CURRENT_UID" != "$PUID" ]; then
	usermod -o -u "$PUID" posterflow
fi

# Extra group ids (comma-separated) for folders other services own, such as Plex's Metadata
# folder; gosu keeps only the groups the user is a member of, so they are added here.
for gid in $(echo "$EXTRA_GROUPS" | tr ',' ' '); do
	getent group "$gid" >/dev/null || groupadd -o -g "$gid" "extra$gid"
	usermod -aG "$(getent group "$gid" | cut -d: -f1)" posterflow
done

chown posterflow:posterflow /config

# Inherited through gosu by the app and every rclone subprocess
umask "$UMASK"

TIMESTAMP=$(date +"%y/%m/%d %H:%M:%S")
printf "%s | INFO     | [    STARTUP    ] • Entrypoint ready (uid=%s gid=%s groups=%s umask=%s)\n" "$TIMESTAMP" "$PUID" "$PGID" "$(id -G posterflow | tr ' ' ',')" "$UMASK"

# Drop to posterflow user and exec the command
exec gosu posterflow "$@"
