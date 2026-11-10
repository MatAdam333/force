#!/bin/sh
# Renders the whole intro as the terminal shows it (half-block pixels, the preview font for text)
# into an mp4, for looking at without a terminal:
#     tools/preview.sh OUT.mp4 [COLSxROWS] [CELL_ASPECT] [FPS]
# Default: a full-screen 16:9 kitty (191x47 cells, 2.29 tall), 30 fps. Needs ffmpeg.
set -eu
out=$1 size=${2:-191x47} aspect=${3:-2.29} fps=${4:-30}
bin=$(dirname "$0")/../target/release/force-intro
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
total=$(awk '/^pub const TOTAL:/ { gsub(/;/, "", $6); print $6 }' "$(dirname "$0")/../src/intro/timing.rs")
n=$(awk -v t="$total" -v f="$fps" 'BEGIN { print int(t * f) }')
seq 0 "$n" | xargs -P "$(nproc)" -I{} sh -c '
    t=$(awk -v i={} -v f='"$fps"' "BEGIN { printf \"%.4f\", i / f }")
    "'"$bin"'" --png "'"$tmp"'/$(printf %05d {}).png" --at "$t" --size '"$size"' --cell-aspect '"$aspect"' --scale 6'
ffmpeg -v error -y -framerate "$fps" -i "$tmp/%05d.png" -vf "pad=ceil(iw/2)*2:ceil(ih/2)*2" \
    -c:v libx264 -pix_fmt yuv420p -crf 18 "$out"
echo "$out: $n frames at $fps fps, $size cells"
