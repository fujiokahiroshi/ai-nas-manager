"""CH1〜4(media_catalog.py)の各fragment代表フレームを生成する。

ai-nas-manager/media/thumbnails/ch{channel}_f{index}.png として保存する
(gitignore対象、media_catalog.pyの_thumb()が参照するパスと一致させること)。
LEGO動画自体はこのマシン固有のOneDriveパスにあるため、他マシンでは実行できない。

実行: cd ai-nas-manager && .venv/bin/python scripts/generate_channel_thumbnails.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import media_catalog
import video_fragmentation

THUMBNAILS_DIR = Path(__file__).resolve().parent.parent / "media" / "thumbnails"


def main() -> None:
    THUMBNAILS_DIR.mkdir(parents=True, exist_ok=True)
    for channel in media_catalog.CHANNELS:
        if not channel.path.exists():
            print(f"CH{channel.channel} ({channel.title}): SKIP, file not found: {channel.path}")
            continue
        for i, frag in enumerate(channel.fragments):
            at = (frag.start + frag.end) / 2
            out_path = THUMBNAILS_DIR / f"ch{channel.channel}_f{i}.png"
            video_fragmentation.extract_frame(channel.path, at, out_path)
            print(f"CH{channel.channel} f{i} @ {at:.1f}s -> {out_path}")


if __name__ == "__main__":
    main()
