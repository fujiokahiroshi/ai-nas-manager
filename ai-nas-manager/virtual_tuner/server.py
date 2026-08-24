"""仮想Tuner: 実機Tuner(Amlogic搭載)のスタンドアロンモード相当を模す、独立プロセスのMCPサーバー。

ai-nas-manager本体のコードには依存しない。streamable-http でネットワーク公開し、
起動時にmDNSで自身をアドバタイズする。将来的に実機Tunerへ差し替える際は、
このプロセスを実機に置き換えるだけで済むことを意図している。
"""

from __future__ import annotations

import argparse
import logging
from datetime import datetime
from typing import Any

from mcp.server.mcpserver import MCPServer

from virtual_tuner.epg import SAMPLE_GUIDE
from virtual_tuner.mdns_advertise import advertise, unadvertise

logger = logging.getLogger(__name__)

mcp = MCPServer("virtual-tuner")

# 実機の想定仕様(電源状態、チャンネル、録画状態など)を模した仮のダミー状態。
# 実機Tunerの正式なフィールド構成が確定次第、更新する(第11章 未確定事項参照)。
_state = {
    "power": "on",
    "channel": None,
    "recording": False,
}


@mcp.tool()
def get_status() -> dict[str, Any]:
    """Tuner自身の状態(power/channel/recording)を返す。"""
    return dict(_state)


@mcp.tool()
def list_channels() -> list[str]:
    """番組表に含まれるチャンネル名の一覧を返す。"""
    seen: list[str] = []
    for program in SAMPLE_GUIDE:
        if program.channel not in seen:
            seen.append(program.channel)
    return seen


@mcp.tool()
def get_program_guide(
    channel: str | None = None,
    genre: str | None = None,
    keyword: str | None = None,
    at: str | None = None,
) -> list[dict[str, Any]]:
    """番組表を取得する(サンプルデータ)。

    channel/genreで絞り込み、keywordはタイトル・概要への部分一致検索、
    atにISO8601形式の時刻を指定するとその時刻に放送中の番組だけを返す。
    条件を何も指定しない場合は全件を返す。
    """
    results = list(SAMPLE_GUIDE)

    if channel:
        results = [p for p in results if p.channel == channel]
    if genre:
        results = [p for p in results if p.genre == genre]
    if keyword:
        results = [p for p in results if keyword in p.title or keyword in p.description]
    if at:
        target = datetime.fromisoformat(at)
        results = [
            p
            for p in results
            if datetime.fromisoformat(p.start) <= target < datetime.fromisoformat(p.end)
        ]

    return [p.as_dict() for p in results]


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="[virtual-tuner] %(message)s")

    parser = argparse.ArgumentParser(description="Virtual Tuner (development stand-in for real hardware)")
    parser.add_argument("--name", default="virtual-tuner", help="mDNSインスタンス名")
    parser.add_argument("--host", default="0.0.0.0", help="バインドするホスト")
    parser.add_argument("--port", type=int, default=8765, help="リッスンポート")
    args = parser.parse_args()

    advertise(name=args.name, port=args.port)
    try:
        mcp.run(transport="streamable-http", host=args.host, port=args.port)
    finally:
        unadvertise()


if __name__ == "__main__":
    main()
