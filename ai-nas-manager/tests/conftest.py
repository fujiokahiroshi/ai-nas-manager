from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="session")
def virtual_tuner_process():
    """テスト用に仮想Tunerを独立プロセスとして起動する(ポート8766、実運用の8765とは分離)。"""
    proc = subprocess.Popen(
        [sys.executable, "-m", "virtual_tuner", "--port", "8766", "--name", "test-tuner"],
        cwd=str(REPO_ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(2.5)  # 起動 + mDNSアドバタイズの安定待ち
    try:
        yield proc
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
