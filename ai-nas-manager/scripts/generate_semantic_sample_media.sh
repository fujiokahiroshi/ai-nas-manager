#!/usr/bin/env bash
# segment_alignment.py / semantic_fragments.py の予備実験用に、
# 「単純な映像」のサンプルをffmpegで生成する。
# 野球以外のシーンも混ぜているのは、現状のタグ付けロジック(semantic_fragments.py)が
# 野球専用のハードコードに近いため、他のシーンでどこまで汎用的に動くかを
# 検証しやすくするため。
#
# 設計: ai-nas-manager/docs/semantic-tagging-experiment.md
#
# 生成物は.gitignoreでリポジトリから除外している(バイナリなので都度このスクリプトで
# 再生成する想定)。前提: ffmpegと日本語フォントがインストール済みであること
# (apt install ffmpeg fonts-noto-cjk)。DejaVu等の欧文フォントだと日本語テキストが
# 豆腐(□)になるので、fonts-noto-cjkの導入が必須。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT_DIR="$SCRIPT_DIR/../media/semantic_samples"
FONT="/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"

mkdir -p "$OUT_DIR"

generate() {
  local name="$1" color="$2" freq="$3" text="$4"
  local out="$OUT_DIR/$name.mp4"
  echo "generating $out ..."
  ffmpeg -y -loglevel error \
    -f lavfi -i "color=c=${color}:size=1280x720:rate=30:duration=10" \
    -f lavfi -i "sine=frequency=${freq}:duration=10" \
    -vf "drawtext=fontfile=${FONT}:text='${text}':fontsize=46:fontcolor=black:x=(w-text_w)/2:y=(h-text_h)/2:box=1:boxcolor=white@0.6:boxborderw=24,drawbox=x='mod(t*100\,w)':y=h-80:w=40:h=40:color=black@0.6:t=fill" \
    -c:v libx264 -pix_fmt yuv420p -c:a aac -shortest \
    "$out"
}

generate "sample01_cooking"  "peachpuff"  300 "台所で大人が料理をしている"
generate "sample02_dog_walk" "lightgreen" 350 "公園で子供が犬の散歩をしている"
generate "sample03_reading"  "lightblue"  400 "子供が静かに絵本を読んでいる"
generate "sample04_soccer"   "lightyellow" 450 "子供たちが公園でサッカーをしている"
generate "sample05_baseball" "lightpink"  500 "子供たちが公園で野球をしている"

echo "done: $(ls "$OUT_DIR"/*.mp4 | wc -l) files in $OUT_DIR"
