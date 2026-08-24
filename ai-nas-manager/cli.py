"""ai-nas-manager CLIエントリポイント。"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from discovery import discover_tuners
from tuner_client import get_status as tuner_get_status

logger = logging.getLogger(__name__)


def _cmd_tuner_list(args: argparse.Namespace) -> int:
    tuners = discover_tuners(timeout_sec=args.timeout)
    if not tuners:
        print("Tunerが見つかりませんでした。")
        return 0
    for t in tuners:
        print(f"{t.name}\t{t.host}:{t.port}")
    return 0


def _cmd_tuner_status(args: argparse.Namespace) -> int:
    tuners = discover_tuners(timeout_sec=args.timeout)
    match = next((t for t in tuners if t.name == args.name), None)
    if match is None:
        print(f"Tuner '{args.name}' が見つかりませんでした(オフラインまたは名称違い)。", file=sys.stderr)
        return 1

    try:
        status = asyncio.run(tuner_get_status(match.host, match.port))
    except Exception as exc:  # noqa: BLE001 - CLIでのエラー表示用
        print(f"Tuner '{args.name}' への接続に失敗しました: {exc}", file=sys.stderr)
        return 1

    for key, value in status.items():
        print(f"{key}: {value}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ai-nas-manager")
    subparsers = parser.add_subparsers(dest="command")

    tuner = subparsers.add_parser("tuner", help="Tuner関連の操作")
    tuner_sub = tuner.add_subparsers(dest="tuner_command")

    list_parser = tuner_sub.add_parser("list", help="ネットワーク上のTunerを一覧表示")
    list_parser.add_argument("--timeout", type=float, default=3.0)
    list_parser.set_defaults(func=_cmd_tuner_list)

    status_parser = tuner_sub.add_parser("status", help="指定したTunerの状態取得")
    status_parser.add_argument("name")
    status_parser.add_argument("--timeout", type=float, default=3.0)
    status_parser.set_defaults(func=_cmd_tuner_status)

    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING, format="[ai-nas-manager] %(message)s")
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        return 1
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
