"""AI NAS manager MCPサーバーの骨組み。

将来的にRockchip NAS上で動かし、メディア管理(整理・分類など)を
自然言語で操作できるようにするための土台。
v-01ではTuner(仮想Tuner)発見・状態取得・番組表取得のMCP連携パターンを追加した。
"""

import anyio

from mcp.server.mcpserver import MCPServer

import tuner_client as _tuner_client
from discovery import TunerInfo, discover_tuners as _discover_tuners

mcp = MCPServer("ai-nas-manager")


@mcp.tool()
def ping() -> str:
    """疎通確認用のダミーツール。"""
    return "pong"


@mcp.tool()
def list_media(path: str = ".") -> str:
    """指定パス配下のメディアファイル一覧を返す(未実装のダミー応答)。"""
    return f"TODO: 未実装。{path} 配下のメディア一覧をここに実装する。"


@mcp.tool()
async def discover_tuners(timeout_sec: float = 3.0) -> list[dict]:
    """mDNSでネットワーク上のTuner(仮想Tunerを含む)を発見する。"""
    tuners = await anyio.to_thread.run_sync(_discover_tuners, timeout_sec)
    return [{"name": t.name, "host": t.host, "port": t.port} for t in tuners]


async def _resolve_tuner(
    tuner_name: str | None, host: str | None, port: int | None
) -> tuple[str, int]:
    """tuner_name(mDNS名前解決)またはhost+port直接指定から接続先を決定する。"""
    if host is not None and port is not None:
        return host, port

    if not tuner_name:
        raise ValueError("tuner_name か host+port のどちらかを指定してください")

    tuners: list[TunerInfo] = await anyio.to_thread.run_sync(_discover_tuners, 3.0)
    match = next((t for t in tuners if t.name == tuner_name), None)
    if match is None:
        raise ValueError(f"Tuner '{tuner_name}' が見つかりませんでした")
    return match.host, match.port


@mcp.tool()
async def get_tuner_status(
    tuner_name: str | None = None,
    host: str | None = None,
    port: int | None = None,
) -> dict:
    """指定したTunerの状態を取得する。tuner_name指定時はmDNSで名前解決する(host+port直接指定も可)。"""
    resolved_host, resolved_port = await _resolve_tuner(tuner_name, host, port)
    return await _tuner_client.get_status(resolved_host, resolved_port)


@mcp.tool()
async def get_tuner_channels(
    tuner_name: str | None = None,
    host: str | None = None,
    port: int | None = None,
) -> list[str]:
    """指定したTunerのチャンネル一覧を取得する。tuner_name指定時はmDNSで名前解決する(host+port直接指定も可)。"""
    resolved_host, resolved_port = await _resolve_tuner(tuner_name, host, port)
    return await _tuner_client.list_channels(resolved_host, resolved_port)


@mcp.tool()
async def get_tuner_program_guide(
    tuner_name: str | None = None,
    host: str | None = None,
    port: int | None = None,
    channel: str | None = None,
    genre: str | None = None,
    keyword: str | None = None,
    at: str | None = None,
) -> list[dict]:
    """指定したTunerの番組表を取得する。channel/genre/keyword/at(ISO8601時刻)で絞り込み可能。

    tuner_name指定時はmDNSで名前解決する(host+port直接指定も可)。
    """
    resolved_host, resolved_port = await _resolve_tuner(tuner_name, host, port)
    return await _tuner_client.get_program_guide(
        resolved_host, resolved_port, channel=channel, genre=genre, keyword=keyword, at=at
    )


if __name__ == "__main__":
    mcp.run()
