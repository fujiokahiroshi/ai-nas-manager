"""仮想メディアチャンネル(CH1〜CH12)の定義。

virtual_tunerのダミーEPGと同じ位置づけで、実機Tuner(Amlogic搭載)に
置き換わるまでの代役。設計: ai-nas-manager/docs/media-renderer-design.md 3.1節。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

MEDIA_DIR = Path(__file__).resolve().parent / "media"
CHANNEL_COUNT = 12


@dataclass(frozen=True)
class MediaChannel:
    channel: int
    title: str
    path: Path


CHANNELS: list[MediaChannel] = [
    MediaChannel(channel=n, title=f"CH{n}", path=MEDIA_DIR / f"ch{n:02d}.mp4")
    for n in range(1, CHANNEL_COUNT + 1)
]

_BY_CHANNEL: dict[int, MediaChannel] = {c.channel: c for c in CHANNELS}


def list_channels() -> list[MediaChannel]:
    """CH1〜CH12の一覧を返す。"""
    return list(CHANNELS)


def get_channel(channel: int) -> MediaChannel:
    """指定チャンネルの定義を返す。存在しない場合はValueErrorを送出する。"""
    try:
        return _BY_CHANNEL[channel]
    except KeyError as e:
        raise ValueError(f"チャンネル {channel} は存在しません(CH1〜CH{CHANNEL_COUNT})") from e
