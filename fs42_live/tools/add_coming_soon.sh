#!/bin/sh
# Adds a placeholder channel that shows a "Coming Soon" slate with the guide music.
#   add_coming_soon.sh <confs dir> <channel number> "<channel name>"
# Restart the fs42-live container afterwards. Replace the file once the channel is real.
set -eu
[ $# -eq 3 ] || { echo "usage: $0 <confs dir> <channel number> \"<channel name>\"" >&2; exit 1; }

python3 - "$@" <<'EOF'
import json, os, re, sys, urllib.parse

confs, number, name = sys.argv[1], int(sys.argv[2]), sys.argv[3]
query = urllib.parse.urlencode({"name": name, "ch": number, "music": "/runtime/guide_music"})
station = {
    "network_name": name,
    "channel_number": number,
    "network_type": "web",
    "hidden": True,  # keep it off the retro guide channel; Jellyfin's guide says "Coming Soon"
    "live_listing": "Coming Soon",
    "web_url": f"/live/coming_soon.html?{query}",
}
path = os.path.join(confs, "coming_soon_" + re.sub(r"\W+", "_", name.lower()).strip("_") + ".json")
with open(path, "w") as f:
    json.dump({"station_conf": station}, f, indent=2)
print(f"wrote {path}")
EOF
