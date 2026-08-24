from datetime import datetime

from virtual_tuner.epg import JST
from virtual_tuner.server import get_program_guide, get_status, list_channels


def test_get_status_returns_provisional_fields() -> None:
    status = get_status()
    assert status["power"] == "on"
    assert status["recording"] is False
    assert "channel" in status


def test_list_channels_returns_all_sample_channels() -> None:
    channels = list_channels()
    assert channels == [
        "NHK総合",
        "NHK Eテレ",
        "日本テレビ",
        "テレビ朝日",
        "TBS",
        "テレビ東京",
        "フジテレビ",
    ]


def test_get_program_guide_no_filter_returns_all() -> None:
    programs = get_program_guide()
    assert len(programs) == 30


def test_get_program_guide_filters_by_channel() -> None:
    programs = get_program_guide(channel="TBS")
    assert len(programs) == 4
    assert all(p["channel"] == "TBS" for p in programs)


def test_get_program_guide_filters_by_keyword() -> None:
    programs = get_program_guide(keyword="ドラマ")
    assert len(programs) == 2
    assert all("ドラマ" in p["title"] or "ドラマ" in p["description"] for p in programs)


def test_get_program_guide_filters_by_time() -> None:
    today_7am = datetime.now(JST).replace(hour=7, minute=0, second=0, microsecond=0)
    programs = get_program_guide(at=today_7am.isoformat())
    titles = {p["title"] for p in programs}
    assert titles == {
        "世界ふれあい紀行",
        "Eテレ午後のアニメ",
        "ZIP!",
        "グッド!モーニング",
        "ひるおび",
        "なないろ日和!",
        "めざましテレビ",
    }
