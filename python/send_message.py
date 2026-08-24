"""Windows11側からMCPメッセージレシーバーへメッセージを送るスケルトンスクリプト。

MCPサーバー(src/index.ts)がローカルで立てているHTTPエンドポイント
(既定ポート: 39217) にJSONをPOSTする。標準ライブラリのみで動作するため、
追加の pip install は不要。

Windows上のPython / WSL上のPython のどちらから実行しても動くよう、接続先ホストは
自動判定する(WSL内で実行された場合はWindowsホストのIPを自動検出)。

使い方:
    python send_message.py "こんにちは"
    python send_message.py "ビルド完了" --source ci-bot
    python send_message.py "テキスト" --url http://127.0.0.1:39217/message
"""

import argparse
import json
import os
import platform
import re
import subprocess
import urllib.error
import urllib.request
from typing import Optional


def _default_host() -> str:
    """WSL上で実行されている場合はWindowsホスト(既定ゲートウェイ)のIPを、
    それ以外はループバックを返す。

    WSL2のNATモードでは、WSL側の /etc/resolv.conf の nameserver はWSL内部の
    DNSスタブでありWindowsホストのTCPには使えないため、代わりに `ip route` の
    デフォルトゲートウェイ(=vEthernet(WSL)アダプタ上のWindowsホストIP)を使う。
    """
    if platform.system() != "Linux":
        return "127.0.0.1"
    try:
        with open("/proc/version") as f:
            if "microsoft" not in f.read().lower():
                return "127.0.0.1"
        out = subprocess.run(
            ["ip", "route"], capture_output=True, text=True, timeout=3
        ).stdout
        m = re.search(r"default via (\S+)", out)
        if m:
            return m.group(1)
    except (OSError, subprocess.SubprocessError):
        pass
    return "127.0.0.1"


DEFAULT_URL = f"http://{_default_host()}:{os.environ.get('MCP_HTTP_PORT', '39217')}/message"


def send_message(text: str, source: Optional[str] = None, url: str = DEFAULT_URL) -> dict:
    payload = {"text": text}
    if source:
        payload["source"] = source

    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Send a message to the MCP message receiver.")
    parser.add_argument("text", help="送信するメッセージ本文")
    parser.add_argument("--source", default="windows11", help="送信元ラベル(任意)")
    parser.add_argument("--url", default=DEFAULT_URL, help="MCPレシーバーのエンドポイントURL")
    args = parser.parse_args()

    try:
        result = send_message(args.text, source=args.source, url=args.url)
        print(f"送信成功: {result}")
    except urllib.error.HTTPError as exc:
        print(f"送信失敗 (HTTP {exc.code}): {exc.read().decode('utf-8', errors='replace')}")
    except urllib.error.URLError as exc:
        print(f"送信失敗: MCPサーバーに接続できません ({exc.reason})")


if __name__ == "__main__":
    main()
