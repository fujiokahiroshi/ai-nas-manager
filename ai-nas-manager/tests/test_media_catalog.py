import pytest

import media_catalog


def test_list_channels_returns_four() -> None:
    channels = media_catalog.list_channels()
    assert len(channels) == 4
    assert [c.channel for c in channels] == [1, 2, 3, 4]


def test_get_channel_valid() -> None:
    c = media_catalog.get_channel(3)
    assert c.channel == 3
    assert c.title == "Puppy"
    assert c.path.name == "MCR-LV-4-3-Puppy.webm"
    assert "Puppy" in c.tag


def test_get_channel_invalid_raises() -> None:
    with pytest.raises(ValueError):
        media_catalog.get_channel(99)


def test_get_channel_has_fragments() -> None:
    c = media_catalog.get_channel(3)
    assert len(c.fragments) == 10
    assert c.fragments[0].start == 0.0
    assert c.fragments[-1].end > c.fragments[0].end


def test_search_channels_matches_title() -> None:
    results = media_catalog.search_channels("gyroboy")
    assert [c.channel for c in results] == [1]


def test_search_channels_matches_tag_text() -> None:
    results = media_catalog.search_channels("色選別")
    assert [c.channel for c in results] == [2]


def test_search_channels_matches_fragment_description() -> None:
    results = media_catalog.search_channels("しゃがんだ")
    assert [c.channel for c in results] == [3]


def test_search_channels_no_match_returns_empty() -> None:
    assert media_catalog.search_channels("存在しないキーワードxyz") == []
