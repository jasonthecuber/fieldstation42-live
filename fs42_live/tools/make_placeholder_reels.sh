#!/bin/sh
# Generate placeholder commercials and bumps (color bars + tone) for a station
# that has no real ones yet. Run inside the fs42-live container:
#   docker exec fs42-live sh fs42_live/tools/make_placeholder_reels.sh catalog/miami_vice "MIAMI VICE"
set -e

DEST="${1:?usage: $0 <content_dir> <station title>}"
TITLE="${2:?usage: $0 <content_dir> <station title>}"
FONT=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf
SIZE=640x480
mkdir -p "$DEST/commercial" "$DEST/bump"

# 30s commercials: SMPTE bars and a 1 kHz tone at different volumes
for n in 1 2 3 4; do
    out="$DEST/commercial/placeholder_spot_$n.mp4"
    [ -f "$out" ] && continue
    ffmpeg -hide_banner -loglevel error \
        -f lavfi -i "smptebars=size=$SIZE:rate=30000/1001:duration=30" \
        -f lavfi -i "sine=frequency=1000:sample_rate=48000:duration=30" \
        -vf "drawtext=fontfile=$FONT:text='YOUR AD HERE #$n':fontsize=40:fontcolor=white:box=1:boxcolor=black@0.7:boxborderw=12:x=(w-tw)/2:y=h*0.7" \
        -af "volume=0.$((n + 1))" \
        -c:v libx264 -preset veryfast -pix_fmt yuv420p -c:a aac -b:a 128k "$out"
done

# 5s bumps: "stay tuned" / "now back to" cards (no apostrophes: drawtext text is single-quoted)
for kind in out in; do
    out="$DEST/bump/placeholder_bump_$kind.mp4"
    [ -f "$out" ] && continue
    if [ "$kind" = out ]; then msg="Stay tuned"; else msg="And now back to"; fi
    ffmpeg -hide_banner -loglevel error \
        -f lavfi -i "gradients=size=$SIZE:rate=30000/1001:duration=5:c0=0xff2d95:c1=0x00c8ff:speed=0.02" \
        -f lavfi -i "anullsrc=sample_rate=48000:channel_layout=stereo:d=5" \
        -vf "drawtext=fontfile=$FONT:text='$TITLE':fontsize=64:fontcolor=white:shadowx=3:shadowy=3:x=(w-tw)/2:y=h*0.38,drawtext=fontfile=$FONT:text='$msg':fontsize=30:fontcolor=white:x=(w-tw)/2:y=h*0.58" \
        -c:v libx264 -preset veryfast -pix_fmt yuv420p -c:a aac -b:a 128k -shortest "$out"
done

ls -l "$DEST/commercial" "$DEST/bump"
