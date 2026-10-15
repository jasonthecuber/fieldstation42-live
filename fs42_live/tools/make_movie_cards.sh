#!/bin/sh
# Makes a movie channel's cards: "Our Feature Presentation" (before each movie), two channel
# idents for the breaks between movies, and a still for any last few seconds.
# Run inside fs42-live from /app:
#   sh fs42_live/tools/make_movie_cards.sh catalog/movies "FS42" "MOVIES" "MOVIE CLASSICS"
set -e
D=$1 NAME=${2:-FS42} WORD=${3:-MOVIES} LONG=${4:-MOVIE CLASSICS}
SERIF=$(fc-list | grep -o '/[^:]*LiberationSerif-Bold.ttf' | head -1)
ITAL=$(fc-list | grep -o '/[^:]*LiberationSerif-BoldItalic.ttf' | head -1)
SANS=$(fc-list | grep -o '/[^:]*LiberationSans-Bold.ttf' | head -1)
mkdir -p "$D/intro" "$D/bump/pre" "$D/bump/post"

chord() {  # a major chord on $1 Hz, swelling in and fading out over $2 seconds
  echo "aevalsrc='0.18*(sin(2*PI*$1*t)+sin(2*PI*$1*1.26*t)+sin(2*PI*$1*1.5*t)+0.5*sin(2*PI*$1*2*t))*min(t/1.2,1)*min(($2-t)/1.5,1)':s=48000:d=$2"
}
BG="gradients=s=640x480:c0=0x0a1a6e:c1=0x000000:x0=320:y0=0:x1=320:y1=480:r=30"
LOGO="drawtext=fontfile=$SERIF:text='$NAME':fontsize=150:fontcolor=0xffd400:x=(w-tw)/2:y=130:shadowx=4:shadowy=4,
drawbox=x=170:y=290:w=300:h=4:color=0xffd400:t=fill"
WORDMARK="drawtext=fontfile=$SANS:text='$(echo "$WORD" | sed 's/./& /g;s/ $//')':fontsize=46:fontcolor=white:x=(w-tw)/2:y=310:shadowx=3:shadowy=3"
ENC="-c:v libx264 -pix_fmt yuv420p -c:a aac -b:a 128k -shortest"

# going into a break
ffmpeg -y -v error -f lavfi -i "$BG:d=5" -f lavfi -i "$(chord 196 5)" \
  -vf "$LOGO,$WORDMARK,fade=in:d=0.6,fade=out:st=4.3:d=0.7" $ENC "$D/bump/pre/ident.mp4"
cp "$D/bump/pre/ident.mp4" "$D/bump/ident.mp4"  # upstream wants a plain bump too
# coming back from one
ffmpeg -y -v error -f lavfi -i "$BG:d=5" -f lavfi -i "$(chord 220 5)" \
  -vf "$LOGO,drawtext=fontfile=$SANS:text='$LONG':fontsize=40:fontcolor=white:x=(w-tw)/2:y=312:shadowx=3:shadowy=3,
drawtext=fontfile=$SANS:text='ALL DAY. ALL NIGHT.':fontsize=22:fontcolor=0xc8d2ff:x=(w-tw)/2:y=372,
fade=in:d=0.6,fade=out:st=4.3:d=0.7" $ENC "$D/bump/post/ident.mp4"
# right before each movie
ffmpeg -y -v error -f lavfi -i "color=c=black:s=640x480:d=6:r=30" -f lavfi -i "$(chord 196 6)" \
  -vf "drawtext=fontfile=$ITAL:text='Our Feature':fontsize=64:fontcolor=0xffd400:x=(w-tw)/2:y=150:shadowx=3:shadowy=3,
drawtext=fontfile=$ITAL:text='Presentation':fontsize=64:fontcolor=0xffd400:x=(w-tw)/2:y=235:shadowx=3:shadowy=3,
drawtext=fontfile=$SANS:text='$NAME $WORD':fontsize=22:fontcolor=0xc8d2ff:x=(w-tw)/2:y=340,
fade=in:st=0.3:d=1,fade=out:st=5:d=1" $ENC "$D/intro/feature_presentation.mp4"
# a still for gaps too short for any commercial (in runtime/, so the catalog skips it)
STILL=runtime/$(basename "$D")_still.png
ffmpeg -y -v error -f lavfi -i "$BG:d=1" -vf "$LOGO,$WORDMARK" -frames:v 1 "$STILL"
echo "Made $D/intro/feature_presentation.mp4, $D/bump/{pre,post}/ident.mp4 and $STILL"
