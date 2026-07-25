#!/bin/sh
set -eu

# Docker named volumes are mounted after image-layer ownership is applied. An
# existing volume can therefore appear as root:root even though the image made
# /app/generated and /app/uploads writable for jobpilot. Repair only these two
# application-owned paths, then drop privileges for every API/worker command.
if [ "$(id -u)" = "0" ]; then
  mkdir -p /app/generated /app/uploads /app/uploads/company_logos
  chown -R jobpilot:jobpilot /app/generated /app/uploads
  exec setpriv --reuid=jobpilot --regid=jobpilot --init-groups -- "$@"
fi

exec "$@"
