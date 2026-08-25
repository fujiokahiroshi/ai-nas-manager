import pytest

import media_catalog


def test_list_channels_returns_twelve() -> None:
    channels = media_catalog.list_channels()
    assert len(channels) == 12
    assert [c.channel for c in channels] == list(range(1, 13))


def test_get_channel_valid() -> None:
    c = media_catalog.get_channel(5)
    assert c.channel == 5
    assert c.title == "CH5"
    assert c.path.name == "ch05.mp4"


def test_get_channel_invalid_raises() -> None:
    with pytest.raises(ValueError):
        media_catalog.get_channel(99)
