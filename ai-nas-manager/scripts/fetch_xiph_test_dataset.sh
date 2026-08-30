#!/usr/bin/env bash
# AI NAS Manager評価用の多様なテスト映像セットを、Xiph.orgの公開研究用テスト素材
# (media.xiph.org/video/derf/)から取得する。
#
# raw YUV4MPEG形式は非常に大きい(1080pで1GB超)ため、ダウンロード直後に
# ffmpegでH.264/MP4へ圧縮し、rawファイルは都度削除することでディスク使用量を
# 抑える。1〜3GB程度のデータセットを目標にする。
#
# 出典・ライセンス: Xiph.orgのderfコレクションは映像コーデック研究・評価目的で
# 公開されている素材(パブリックドメインまたは同等の利用条件)。
# 設計: ai-nas-manager/docs/semantic-tagging-experiment.md

set -euo pipefail

BASE_URL="https://media.xiph.org/video/derf/y4m"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT_DIR="$SCRIPT_DIR/../media/test_dataset"
RAW_TMP="$OUT_DIR/_raw_tmp"

mkdir -p "$OUT_DIR" "$RAW_TMP"

# name:category:crf(圧縮品質、低いほど高画質・大容量)
ITEMS=(
  "foreman_qcif:person_face:23"
  "carphone_qcif:person_face:23"
  "claire_qcif:person_face:23"
  "bus_cif:traffic:23"
  "highway_cif:traffic:23"
  "pedestrian_area_1080p25:crowd:26"
)

for item in "${ITEMS[@]}"; do
  IFS=':' read -r name category crf <<< "$item"
  raw="$RAW_TMP/${name}.y4m"
  out="$OUT_DIR/${name}.mp4"

  if [[ -f "$out" ]]; then
    echo "skip (already exists): $out"
    continue
  fi

  echo "downloading ${name}.y4m ..."
  curl -sL -w "  HTTP %{http_code}, %{size_download} bytes\n" \
    -o "$raw" "$BASE_URL/${name}.y4m"

  echo "encoding -> ${name}.mp4 (category=${category}, crf=${crf}) ..."
  ffmpeg -y -loglevel error -i "$raw" \
    -c:v libx264 -preset medium -crf "$crf" -pix_fmt yuv420p \
    "$out"

  rm -f "$raw"
  echo "  done: $(du -h "$out" | cut -f1)"
done

rmdir "$RAW_TMP" 2>/dev/null || true

echo "=== summary ==="
du -h "$OUT_DIR"/*.mp4 2>/dev/null
echo "total: $(du -sh "$OUT_DIR" | cut -f1)"
