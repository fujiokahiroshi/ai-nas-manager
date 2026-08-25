"""仮想メディアチャンネル(CH1〜CH4)の定義。

もともとCH1〜12は色バー+チャンネル番号だけのダミー映像(virtual_tunerの
ダミーEPGと同じ位置づけ、実機Tunerに置き換わるまでの代役)だったが、
それではtag(意味づけ)を付けても内容の乏しさが露呈するだけだったため、
実際に意味のある内容を持つLEGO Mindstorms EV3のロボットデモ映像
(ユーザーのOneDrive上、このマシン固有のパス)に差し替えた。
本数の分だけCH1, CH2, ...と割り付けている。

各tagは、video_fragmentation.pyの段階1(境界検出)を各動画に実行した上で、
代表フレームをClaudeが実際に見て言語化・統合したもの(2026-08-25、
APIキーなしでこのセッション内で手動実施)。
詳細: ai-nas-manager/docs/semantic-tagging-experiment.md 6節。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# このマシン固有のパス(ユーザーのOneDrive)。他マシンでは存在しない前提。
_LEGO_VIDEO_DIR = Path(
    r"/mnt/c/Users/yukik/OneDrive/vivado_axi_vip_test/ドキュメント/"
    r"LEGO Education EV3 Content/videos/core-models"
)


@dataclass(frozen=True)
class MediaChannel:
    channel: int
    title: str
    path: Path
    tag: str


CHANNELS: list[MediaChannel] = [
    MediaChannel(
        channel=1,
        title="GyroBoy",
        path=_LEGO_VIDEO_DIR / "MCR-LV-4-1-GyroBoy.webm",
        tag=(
            "LEGO Mindstorms EV3「GyroBoy」の紹介映像。センサー部品のクローズアップ"
            "から始まり、人の手が2輪の本体を組み立て・調整し、最後に手を離した状態で"
            "2輪だけで自立し倒れずにバランスを取る様子を見せる、自己平衡ロボットの"
            "デモンストレーション。"
        ),
    ),
    MediaChannel(
        channel=2,
        title="ColorSorter",
        path=_LEGO_VIDEO_DIR / "MCR-LV-4-2-ColorSorter.webm",
        tag=(
            "LEGO Mindstorms EV3「ColorSorter」の紹介映像。4色(青・緑・黄・赤)の"
            "パーツが入ったグラスの上にアーム付きロボットが構える引き画から始まり、"
            "EV3画面のカウンター表示・ボタン操作のクローズアップを挟んで、アームが"
            "色付きパーツをグラスへ仕分け(投入)する様子を見せる、色選別ロボットの"
            "デモンストレーション。"
        ),
    ),
    MediaChannel(
        channel=3,
        title="Puppy",
        path=_LEGO_VIDEO_DIR / "MCR-LV-4-3-Puppy.webm",
        tag=(
            "LEGO Mindstorms EV3「Puppy」ロボットの紹介デモ映像。ロボット全体の"
            "引き画から始まり、人の手によるパーツ操作、表情を表す液晶画面の"
            "クローズアップ、ロボットの姿勢変化(立ち→しゃがみ)を編集でつないだ、"
            "実写の製品デモンストレーション。"
        ),
    ),
    MediaChannel(
        channel=4,
        title="RobotArmH25",
        path=_LEGO_VIDEO_DIR / "MCR-LV-4-4-RobotArmH25.webm",
        tag=(
            "LEGO Mindstorms EV3「RobotArmH25」の紹介映像。据え置き型のクレーン状"
            "アームと対象物(黒い円盤)の引き画から始まり、EV3画面の「?」表示と"
            "ボタン操作のクローズアップを挟んで、アームが動作(モーションブラーを"
            "伴う動き)する様子を見せる、ロボットアームのデモンストレーション。"
        ),
    ),
]

CHANNEL_COUNT = len(CHANNELS)

_BY_CHANNEL: dict[int, MediaChannel] = {c.channel: c for c in CHANNELS}


def list_channels() -> list[MediaChannel]:
    """CH1〜CH4の一覧を返す。"""
    return list(CHANNELS)


def get_channel(channel: int) -> MediaChannel:
    """指定チャンネルの定義を返す。存在しない場合はValueErrorを送出する。"""
    try:
        return _BY_CHANNEL[channel]
    except KeyError as e:
        raise ValueError(f"チャンネル {channel} は存在しません(CH1〜CH{CHANNEL_COUNT})") from e
