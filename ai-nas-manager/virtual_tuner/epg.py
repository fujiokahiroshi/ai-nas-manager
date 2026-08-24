"""番組表(EPG)のデータ構造とサンプルデータ生成。

実機Tunerの番組表取得仕様は未確定のため、仮想Tunerでは固定のサンプルデータを
チャンネル×時間帯で敷き詰めて生成する。フィールド構成はシンプルなdict互換の
構造にしてあり、実機に合わせて後から調整しやすいようにしている。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

JST = timezone(timedelta(hours=9))


@dataclass(frozen=True)
class Program:
    channel: str
    channel_number: int
    title: str
    start: str  # ISO8601 (JST)
    end: str  # ISO8601 (JST)
    genre: str
    description: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


## 東京キー局のリモコン番号に合わせたNHK2波+民放5局のラインナップ。
_CHANNELS = [
    (1, "NHK総合"),
    (2, "NHK Eテレ"),
    (4, "日本テレビ"),
    (5, "テレビ朝日"),
    (6, "TBS"),
    (7, "テレビ東京"),
    (8, "フジテレビ"),
]

# (タイトル, ジャンル, 概要, 放送時間[分])
_TEMPLATES: dict[str, list[tuple[str, str, str, int]]] = {
    "NHK総合": [
        ("ニュース845", "ニュース", "最新のニュースと天気予報をお届けします。", 30),
        ("世界ふれあい紀行", "ドキュメンタリー", "ヨーロッパ各地を巡る紀行番組。", 60),
        ("あさイチ", "情報", "暮らしに役立つ情報を生放送でお届け。", 60),
        ("大河ドラマ 星の刻", "ドラマ", "戦国時代を舞台にした本格時代劇。", 45),
        ("クローズアップ現代", "報道", "社会の今を掘り下げる報道番組。", 30),
    ],
    "NHK Eテレ": [
        ("おかあさんといっしょ", "教育", "親子で楽しむ幼児向け番組。", 25),
        ("ピタゴラスイッチ", "教育", "身の回りの科学の仕組みを楽しく学ぶ番組。", 15),
        ("Eテレ午後のアニメ", "アニメ", "子供向けアニメの再放送枠。", 30),
        ("高校講座", "教育", "学習支援番組。", 30),
        ("no art, no life", "ドキュメンタリー", "アートをテーマにした教養番組。", 50),
    ],
    "日本テレビ": [
        ("ZIP!", "情報", "朝の情報番組。", 90),
        ("バラいろダンディ", "バラエティ", "旬の話題をトークで深掘り。", 60),
        ("金曜ロードショー", "映画", "話題の映画を地上波初放送。", 120),
        ("世界の果てまでイッテQ", "バラエティ", "芸能人が世界各地に挑戦する人気番組。", 55),
    ],
    "テレビ朝日": [
        ("グッド!モーニング", "情報", "朝の情報生放送番組。", 110),
        ("モーニングショー", "情報", "時事問題を専門家と議論する朝の番組。", 80),
        ("サンデーアニメ劇場", "アニメ", "人気アニメの放送枠。", 30),
        ("報道ステーション", "報道", "夜のニュース番組。", 65),
    ],
    "TBS": [
        ("ひるおび", "情報", "昼の情報生放送番組。", 120),
        ("水曜日のダウンタウン", "バラエティ", "検証系人気バラエティ。", 54),
        ("VIVANT 総集編", "ドラマ", "大ヒットドラマの総集編を放送。", 90),
        ("報道特集", "報道", "深掘り取材による調査報道番組。", 54),
    ],
    "テレビ東京": [
        ("モーニングサテライト", "経済", "経済ニュースを中心とした朝の情報番組。", 60),
        ("なないろ日和!", "情報", "日替わりで各地の魅力を紹介する情報番組。", 55),
        ("YOUは何しに日本へ?", "バラエティ", "来日外国人に密着する人気番組。", 54),
        ("ゆうがたサテライト", "経済", "夕方の経済ニュース番組。", 30),
    ],
    "フジテレビ": [
        ("めざましテレビ", "情報", "朝の情報番組の定番。", 105),
        ("ノンストップ!", "情報", "昼の情報生放送番組。", 105),
        ("サザエさん", "アニメ", "国民的人気アニメ。", 30),
        ("ザ・ノンフィクション", "ドキュメンタリー", "市井の人々を追うドキュメンタリー番組。", 55),
    ],
}


def generate_sample_guide(base_time: datetime | None = None) -> list[Program]:
    """チャンネルごとに番組を隙間なく敷き詰めたサンプル番組表を生成する。"""
    if base_time is None:
        base_time = datetime.now(JST).replace(hour=6, minute=0, second=0, microsecond=0)

    programs: list[Program] = []
    for number, name in _CHANNELS:
        cursor = base_time
        for title, genre, description, duration_min in _TEMPLATES[name]:
            start = cursor
            end = cursor + timedelta(minutes=duration_min)
            programs.append(
                Program(
                    channel=name,
                    channel_number=number,
                    title=title,
                    start=start.isoformat(),
                    end=end.isoformat(),
                    genre=genre,
                    description=description,
                )
            )
            cursor = end
    return programs


# プロセス起動時に一度だけ生成し、以降はメモリ上に保持する(サンプル固定データ)。
SAMPLE_GUIDE: list[Program] = generate_sample_guide()
