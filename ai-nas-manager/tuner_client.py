"""発見されたTunerのMCPサーバーに接続し、各種コマンドを呼び出すクライアント。"""

from __future__ import annotations

import json
import logging
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

logger = logging.getLogger(__name__)


async def _call_tool(
    host: str,
    port: int,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
    path: str = "/mcp",
) -> Any:
    """指定したTunerに接続し、任意のツールを呼び出して結果を返す。"""
    url = f"http://{host}:{port}{path}"
    logger.info("calling %s on tuner at %s", tool_name, url)
    async with streamable_http_client(url) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            result = await session.call_tool(tool_name, arguments or {})

            if result.structured_content is not None:
                sc = result.structured_content
                # list/str等の非dict戻り値は {"result": ...} でラップされるので剥がす。
                if isinstance(sc, dict) and set(sc.keys()) == {"result"}:
                    return sc["result"]
                return sc

            text = "".join(getattr(c, "text", "") for c in result.content)
            try:
                return json.loads(text)
            except (json.JSONDecodeError, TypeError, ValueError):
                return {"raw": text}


async def get_status(host: str, port: int) -> dict[str, Any]:
    """get_statusツールを呼び出し、Tunerの状態を取得する。"""
    return await _call_tool(host, port, "get_status")


async def list_channels(host: str, port: int) -> list[str]:
    """list_channelsツールを呼び出し、チャンネル一覧を取得する。"""
    return await _call_tool(host, port, "list_channels")


async def get_program_guide(
    host: str,
    port: int,
    channel: str | None = None,
    genre: str | None = None,
    keyword: str | None = None,
    at: str | None = None,
) -> list[dict[str, Any]]:
    """get_program_guideツールを呼び出し、番組表を取得する。"""
    return await _call_tool(
        host,
        port,
        "get_program_guide",
        {"channel": channel, "genre": genre, "keyword": keyword, "at": at},
    )
