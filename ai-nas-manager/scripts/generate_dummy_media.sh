#!/usr/bin/env bash
# CH1〜CH12用のダミー動画(media_catalog.pyが参照する ai-nas-manager/media/ch{01..12}.mp4)を
# ffmpegで生成する。virtual_tunerのダミーEPGと同じ位置づけの、実機Tunerに置き換わるまでの代役。
#
# 設計: ai-nas-manager/docs/media-renderer-design.md 3.1節
#
# 生成物は.gitignoreでリポジトリから除外している(バイナリなので都度このスクリプトで
# 再生成する想定)。前提: ffmpegがインストール済みであること(apt install ffmpeg)。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MEDIA_DIR="$SCRIPT_DIR/../media"
FONT="/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

mkdir -p "$MEDIA_DIR"

for n in $(seq -w 1 12); do
  channel=$((10#$n))
  freq=$((300 + channel * 20))
  out="$MEDIA_DIR/ch$n.mp4"
  echo "generating $out (CH$channel, ${freq}Hz)..."
  ffmpeg -y -loglevel error \
    -f lavfi -i "testsrc=size=1280x720:rate=30:duration=30" \
    -f lavfi -i "sine=frequency=${freq}:duration=30" \
    -vf "drawtext=fontfile=${FONT}:text='CH${channel}':fontsize=160:fontcolor=white:x=(w-text_w)/2:y=(h-text_h)/2:box=1:boxcolor=black@0.5:boxborderw=30" \
    -c:v libx264 -pix_fmt yuv420p -c:a aac -shortest \
    "$out"
done

echo "done: $(ls "$MEDIA_DIR"/*.mp4 | wc -l) files in $MEDIA_DIR"
