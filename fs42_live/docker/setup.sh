#!/bin/sh
# Sets up fieldstation42-live on a fresh box: writes .env, creates the data folders,
# adds the starter channels (TV Guide on 3, Weather on 8), builds and starts the
# containers, and prints the URLs for Jellyfin.
#
#   ./setup.sh            asks for each setting, suggesting a default
#
# Any setting can be given ahead of time to skip its question, e.g.
#   MEDIA_ROOT=/mnt/media WEATHER_LOCATION="Austin TX USA" ./setup.sh
# Safe to run again: it keeps an existing .env and never overwrites a channel config.
set -eu
cd "$(dirname "$0")"
REPO=$(cd ../.. && pwd)

say() { printf '\n== %s\n' "$*"; }
die() { printf '\nSetup stopped: %s\n' "$*" >&2; exit 1; }

# ask VAR "question" default -- keeps VAR if it's already set
ask() {
    eval "current=\${$1:-}"
    if [ -z "$current" ]; then
        printf '%s [%s]: ' "$2" "$3"
        read -r answer || answer=""
        current=${answer:-$3}
    fi
    eval "$1=\$current"
}

say "Checking requirements"
command -v docker >/dev/null || die "Docker isn't installed. Install it (https://docs.docker.com/engine/install/), then run this again."
docker info >/dev/null 2>&1 || die "can't talk to Docker. Add yourself to the docker group (sudo usermod -aG docker $USER), log out and back in, then run this again."
docker compose version >/dev/null 2>&1 || die "the Docker Compose plugin is missing (package docker-compose-plugin)."
command -v python3 >/dev/null || die "python3 is needed to write the channel configs."
[ -e /dev/dri/renderD128 ] || echo "No /dev/dri/renderD128: no Intel Quick Sync here, so channels will encode on the CPU."
echo "OK"

if [ -f .env ]; then
    say "Using the existing settings in $(pwd)/.env"
    set -a; . ./.env; set +a
else
    say "Settings (press Enter to accept the suggestion)"
    host_tz=$(timedatectl show -p Timezone --value 2>/dev/null || cat /etc/timezone 2>/dev/null || echo America/Chicago)
    ask FS42_DATA "Folder for FieldStation42's data (configs, catalogs, schedules)" "$HOME/fs42-data"
    ask MEDIA_ROOT "Folder that holds your shows and movies" "/mnt/storage"
    ask TZ "Viewers' time zone (should match Jellyfin's)" "$host_tz"
    ask FS42_PORT "Port for the web console and streams" "4242"
    [ -d "$MEDIA_ROOT" ] || die "$MEDIA_ROOT doesn't exist."
    [ -f "/usr/share/zoneinfo/$TZ" ] || die "unknown time zone $TZ (expected something like America/Chicago)."
    render_gid=$(getent group render | cut -d: -f3 || true)
    video_gid=$(getent group video | cut -d: -f3 || true)
    cat > .env <<EOF
# Written by setup.sh; see .env.example for what each setting means
FS42_DATA=$FS42_DATA
MEDIA_ROOT=$MEDIA_ROOT
FS42_UID=$(id -u)
FS42_GID=$(id -g)
RENDER_GID=${render_gid:-991}
VIDEO_GID=${video_gid:-44}
TZ=$TZ
FS42_PORT=$FS42_PORT
EOF
    # a Jellyfin API key left by the media box installer: refresh Jellyfin's guide on schedule changes
    [ -f "$HOME/.config/jellyfin-rescan.key" ] && echo "JELLYFIN_KEY_FILE=$HOME/.config/jellyfin-rescan.key" >> .env
    echo "Wrote $(pwd)/.env"
fi

say "Creating data folders in $FS42_DATA"
mkdir -p "$FS42_DATA/confs" "$FS42_DATA/catalog" "$FS42_DATA/runtime/guide_music" \
    "$FS42_DATA/runtime/weather_music" "$FS42_DATA/runtime/prevue_music"
echo "OK"

say "Starter channels"
CONFS="$FS42_DATA/confs"
if [ -f "$CONFS/guide.json" ]; then
    echo "TV Guide: already set up"
else
    cp "$REPO/fs42_live/examples/stations/guide.json" "$CONFS/"
    echo "TV Guide: channel 3"
fi
if [ -f "$CONFS/preview_guide.json" ]; then
    echo "Preview Guide: already set up"
else
    cp "$REPO/fs42_live/examples/stations/preview_guide.json" "$CONFS/"
    echo "Preview Guide: channel 2"
fi
if [ -f "$CONFS/weather.json" ]; then
    echo "Weather: already set up"
else
    ask WEATHER_LOCATION "Town for the weather channel (city, state, country)" "Springfield MO USA"
    python3 - "$REPO/fs42_live/examples/stations/weather.json" "$CONFS/weather.json" "$WEATHER_LOCATION" <<'EOF'
import json, sys, urllib.parse
src, dst, location = sys.argv[1:]
with open(src) as f:
    conf = json.load(f)
station = conf["station_conf"]
url = urllib.parse.urlsplit(station["web_url"])
query = dict(urllib.parse.parse_qsl(url.query))
query["latLonQuery"] = location
station["web_url"] = urllib.parse.urlunsplit(url._replace(query=urllib.parse.urlencode(query, quote_via=urllib.parse.quote)))
with open(dst, "w") as f:
    json.dump(conf, f, indent=2)
EOF
    echo "Weather: channel 8, $WEATHER_LOCATION"
fi

say "Building and starting the containers (the first build takes several minutes)"
docker compose up -d --build

say "Background music"
# WeatherStar comes with a few smooth-jazz tracks; they suit the guides too, until
# someone puts the real Prevue themes in prevue_music (SETUP.md says where)
for dir in weather_music guide_music prevue_music; do
    if ls "$FS42_DATA/runtime/$dir" 2>/dev/null | grep -q .; then
        echo "$dir: already has music"
    else
        docker cp "fs42-ws4kp:/usr/share/nginx/html/music/default/." "$FS42_DATA/runtime/$dir/" \
            && echo "$dir: added WeatherStar's music" || echo "$dir: couldn't copy music; add MP3s there yourself"
    fi
done

say "Waiting for the server"
url="http://localhost:$FS42_PORT"
tries=0
until curl -fs -o /dev/null "$url/live/status"; do
    tries=$((tries + 1))
    [ $tries -lt 60 ] || die "the server didn't come up; see: docker logs fs42-live"
    sleep 2
done
echo "OK"

ip=$(hostname -I 2>/dev/null | cut -d" " -f1)
base="http://${ip:-this-box}:$FS42_PORT"
cat <<EOF

All set. Next steps:

 1. Web console: $base/
    Stations -> add a station for each show channel, then build its catalog.
    Schedules are built and kept a week ahead automatically.
    The Live page shows what's streaming.

 2. Jellyfin -> Dashboard -> Live TV:
    Tuner devices -> add "M3U Tuner":   $base/live/channels.m3u
    TV guide data providers -> XMLTV:   $base/live/xmltv.xml
    Then refresh the guide data.

Placeholder channels:  ../tools/add_coming_soon.sh "$CONFS" 4 "Classic Cartoons" Cartoons
(then: docker restart fs42-live)
EOF
