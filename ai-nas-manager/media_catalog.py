"""仮想メディアチャンネル(CH1〜CH4)の定義。

もともとCH1〜12は色バー+チャンネル番号だけのダミー映像(virtual_tunerの
ダミーEPGと同じ位置づけ、実機Tunerに置き換わるまでの代役)だったが、
それではtag(意味づけ)を付けても内容の乏しさが露呈するだけだったため、
実際に意味のある内容を持つLEGO Mindstorms EV3のロボットデモ映像
(ユーザーのOneDrive上、このマシン固有のパス)に差し替えた。
本数の分だけCH1, CH2, ...と割り付けている。

各tag・fragmentsは、video_fragmentation.pyの段階1(境界検出)を各動画に
実行した上で、代表フレームをClaudeが実際に見て言語化・統合したもの
(2026-08-25、APIキーなしでこのセッション内で手動実施)。
詳細: ai-nas-manager/docs/semantic-tagging-experiment.md 6節・8節。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# このマシン固有のパス(ユーザーのOneDrive)。他マシンでは存在しない前提。
_LEGO_VIDEO_DIR = Path(
    r"/mnt/c/Users/yukik/OneDrive/vivado_axi_vip_test/ドキュメント/"
    r"LEGO Education EV3 Content/videos/core-models"
)

# fragment単位の永続サムネイル置き場。register_media(server.py)が生成する
# ライブラリ側のサムネイルと同じディレクトリに揃える。ここではCH1〜4向けに
# 各fragmentの区間中間時点のフレームを事前生成済み(scripts/で都度再生成)。
_THUMBNAILS_DIR = Path(__file__).resolve().parent / "media" / "thumbnails"


def _thumb(channel: int, index: int) -> Path:
    return _THUMBNAILS_DIR / f"ch{channel}_f{index}.png"


@dataclass(frozen=True)
class FragmentTag:
    """段階1(境界検出)で得られた1区間と、その区間の言語化。"""

    start: float
    end: float
    description: str
    thumbnail_path: Path | None = None


@dataclass(frozen=True)
class MediaChannel:
    channel: int
    title: str
    path: Path
    tag: str
    fragments: list[FragmentTag]


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
        fragments=[
            FragmentTag(0.0, 2.0, "センサー部品(ジャイロセンサーのサブアセンブリ)のクローズアップ。白背景で、組み立て前のパーツ紹介。", thumbnail_path=_thumb(1, 0)),
            FragmentTag(2.0, 18.2, "人の手が2輪のロボット本体を持ち、組み立て・調整をしている。", thumbnail_path=_thumb(1, 1)),
            FragmentTag(18.2, 46.7, "ロボットが人の手を離れて自立し、2輪だけでバランスを取りながら立っている(自己平衡のデモ)。", thumbnail_path=_thumb(1, 2)),
        ],
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
        fragments=[
            FragmentTag(0.0, 6.0, "アーム付きロボットが、4色のパーツが1つずつ入ったグラス4個の上に構えている引き画。", thumbnail_path=_thumb(2, 0)),
            FragmentTag(6.0, 10.6, "EV3画面のクローズアップ。右矢印アイコンと「# = 0」というカウンター表示。", thumbnail_path=_thumb(2, 1)),
            FragmentTag(10.6, 15.2, "最初とほぼ同じ引き画(内容重複、境界検出の再検出と見られる)。", thumbnail_path=_thumb(2, 2)),
            FragmentTag(15.2, 25.0, "人の手がEV3のボタンを押すクローズアップ。プログラム開始の操作。", thumbnail_path=_thumb(2, 3)),
            FragmentTag(25.0, 38.4, "アームが動作し、黄色いパーツをグラスへ投入する様子(モーションブラーあり)。", thumbnail_path=_thumb(2, 4)),
        ],
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
        fragments=[
            FragmentTag(0.0, 2.24, "ロボット頭部の極端なクローズアップ、斜めアングル(冒頭カット)。", thumbnail_path=_thumb(3, 0)),
            FragmentTag(2.24, 7.08, "ロボット全体の引き画、静止した状態。", thumbnail_path=_thumb(3, 1)),
            FragmentTag(7.08, 11.16, "直前とほぼ同一の引き画(内容重複、境界検出の再検出と見られる)。", thumbnail_path=_thumb(3, 2)),
            FragmentTag(11.16, 23.60, "境界検出されなかった区間。ロボットが静止、または大きな変化のない状態が続く。", thumbnail_path=_thumb(3, 3)),
            FragmentTag(23.60, 26.20, "手が画面に入ってくる(モーションブラーあり)。人の介入が始まる。", thumbnail_path=_thumb(3, 4)),
            FragmentTag(26.20, 28.36, "手によるパーツ操作の継続。", thumbnail_path=_thumb(3, 5)),
            FragmentTag(28.36, 33.72, "顔(液晶画面の表情表示部)の極端なクローズアップにカット。", thumbnail_path=_thumb(3, 6)),
            FragmentTag(33.72, 40.88, "引き画に戻り、ロボットが立ち姿勢からしゃがんだ姿勢に変化。", thumbnail_path=_thumb(3, 7)),
            FragmentTag(40.88, 45.00, "再び顔のクローズアップ、色味も変化。", thumbnail_path=_thumb(3, 8)),
            FragmentTag(45.00, 48.05, "引き画、しゃがみ姿勢のまま締めくくり。", thumbnail_path=_thumb(3, 9)),
        ],
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
        fragments=[
            FragmentTag(0.0, 5.9, "据え置き型のクレーン状アームと、対象物(黒い円盤)の引き画。", thumbnail_path=_thumb(4, 0)),
            FragmentTag(5.9, 8.3, "EV3画面のクローズアップ。「?」という疑問符のアイコン表示。", thumbnail_path=_thumb(4, 1)),
            FragmentTag(8.3, 19.1, "人の手がボタンを操作するクローズアップ。", thumbnail_path=_thumb(4, 2)),
            FragmentTag(19.1, 22.3, "再度手のクローズアップ、画面には引き続き「?」表示。", thumbnail_path=_thumb(4, 3)),
            FragmentTag(22.3, 31.4, "アームが動作(モーションブラーを伴う動き)する様子の引き画。", thumbnail_path=_thumb(4, 4)),
        ],
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


def search_channels(keyword: str) -> list[MediaChannel]:
    """タイトル・tag・fragmentの説明文にkeywordを含むチャンネルを返す(大小文字無視)。"""
    needle = keyword.lower()
    matched: list[MediaChannel] = []
    for c in CHANNELS:
        haystack = c.title + c.tag + "".join(f.description for f in c.fragments)
        if needle in haystack.lower():
            matched.append(c)
    return matched
