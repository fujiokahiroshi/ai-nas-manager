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
