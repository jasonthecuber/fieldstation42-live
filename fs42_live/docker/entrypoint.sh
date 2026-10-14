#!/bin/sh
# Seed the mounted runtime/ and catalog/ volumes the way install.sh would,
# without overwriting anything that already exists.
set -e
cd /app

mkdir -p runtime catalog confs

for f in static.mp4 standby.png brb.png off_air_pattern.mp4 signoff.mp4; do
    [ -f "runtime/$f" ] || cp "docs/$f" runtime/
done
[ -d runtime/logo_images ] || cp -r docs/logo_images runtime/
[ -f runtime/channel.socket ] || touch runtime/channel.socket

exec "$@"
