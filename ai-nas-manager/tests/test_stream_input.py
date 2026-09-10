import pytest

from stream_input import RTSPInputConfig, ReconnectableFFmpegStream


def test_rtsp_input_config_rejects_invalid_values() -> None:
    with pytest.raises(ValueError):
        RTSPInputConfig(sample_fps=0)


def test_ffmpeg_input_rejects_invalid_dimensions() -> None:
    with pytest.raises(ValueError):
        ReconnectableFFmpegStream("rtsp://example/test", "ffmpeg", width=0)
