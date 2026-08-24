import pytest

from tuner_client import get_status


@pytest.mark.anyio
async def test_get_status_returns_expected_fields(virtual_tuner_process) -> None:
    status = await get_status("127.0.0.1", 8766)
    assert status["power"] == "on"
    assert status["recording"] is False


@pytest.mark.anyio
async def test_get_status_raises_when_unreachable() -> None:
    # ポート8767は本テストスイート内では誰も待ち受けていない想定(未接続を検証)。
    with pytest.raises(Exception):
        await get_status("127.0.0.1", 8767)
