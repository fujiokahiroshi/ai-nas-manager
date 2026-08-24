"""Claude(MCPサーバー)からのメッセージをLinux(WSL)側で受け取るポーリングスケルトン。

MCPサーバー(src/index.ts)の GET /outbox を定期的にポーリングし、
Claudeが send_to_linux ツールで送ったメッセージを受け取って表示する。
標準ライブラリのみで動作するため、追加の pip install は不要。

受け取った後の処理(通知を出す、ファイルに書く、コマンドを実行するなど)は
on_message() を書き換えて実装する。

使い方:
    python3 receive_message.py
    python3 receive_message.py --interval 2
    python3 receive_message.py --url http://172.28.112.1:39217/outbox
"""

import argparse
import json
import platform
import re
import subprocess
import time
import urllib.error
import urllib.request


def _default_host() -> str:
    """send_message.pyと同じロジック: WSL上ならWindowsホストのIPを、それ以外はループバックを返す。"""
    if platform.system() != "Linux":
        return "127.0.0.1"
    try:
        with open("/proc/version") as f:
            if "microsoft" not in f.read().lower():
                return "127.0.0.1"
        out = subprocess.run(["ip", "route"], capture_output=True, text=True, timeout=3).stdout
        m = re.search(r"default via (\S+)", out)
        if m:
            return m.group(1)
    except (OSError, subprocess.SubprocessError):
        pass
    return "127.0.0.1"


DEFAULT_URL = f"http://{_default_host()}:39217/outbox"


def on_message(message: dict) -> None:
    """メッセージ受信時に呼ばれる。ここを書き換えて任意の処理を実装する。"""
    print(f"[{message['sentAt']}] {message['text']}")


def poll_once(url: str) -> list:
    request = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(request, timeout=5) as response:
        data = json.loads(response.read().decode("utf-8"))
        return data.get("messages", [])


def main() -> None:
    parser = argparse.ArgumentParser(description="Poll the MCP server for messages from Claude.")
    parser.add_argument("--url", default=DEFAULT_URL, help="MCPサーバーのoutboxエンドポイントURL")
    parser.add_argument("--interval", type=float, default=3.0, help="ポーリング間隔(秒)")
    args = parser.parse_args()

    print(f"polling {args.url} every {args.interval}s (Ctrl+C to stop)")
    while True:
        try:
            messages = poll_once(args.url)
            for message in messages:
                on_message(message)
        except urllib.error.URLError as exc:
            print(f"polling failed: {exc}")
        time.sleep(args.interval)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
