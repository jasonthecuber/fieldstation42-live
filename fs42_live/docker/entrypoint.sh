#!/bin/sh
# Seed the mounted runtime/ and catalog/ volumes the way install.sh would,
# without overwriting anything that already exists.
set -e
cd /app

mkdir -p runtime catalog confs

for f in static.mp4 standby.png off_air_pattern.mp4 signoff.mp4; do
    [ -f "runtime/$f" ] || cp "docs/$f" runtime/
done
# the filler between shows: "We'll Be Right Back" (kept in the middle, so any zoom shows it
# whole) rather than FieldStation42's "Powered By" card, which reads like a fault
[ -f runtime/brb.png ] || cp docs/right_back.png runtime/brb.png
[ -d runtime/logo_images ] || cp -r docs/logo_images runtime/
[ -f runtime/channel.socket ] || touch runtime/channel.socket

exec "$@"
