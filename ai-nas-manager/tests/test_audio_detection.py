import numpy as np
import pytest

from audio_detection import AudioChangeConfig, AudioChangeDetector, AudioFeatureTimeline


def pcm_tone(amplitude: float, frequency: float = 440.0, sample_rate: int = 16_000) -> np.ndarray:
    time = np.arange(sample_rate // 2) / sample_rate
    return (np.sin(2 * np.pi * frequency * time) * amplitude * 32767).astype(np.int16)


def test_silence_to_tone_is_an_audio_onset() -> None:
    detector = AudioChangeDetector()
    quiet = detector.analyze(0, np.zeros(8_000, dtype=np.int16))
    onset = detector.analyze(500, pcm_tone(0.8))
    assert quiet.label == "silence"
    assert onset.active is True
    assert onset.onset is True
    assert onset.change_score >= 0.8
    assert onset.label == "impact_candidate"


def test_steady_tone_does_not_keep_retriggering() -> None:
    detector = AudioChangeDetector()
    tone = pcm_tone(0.25)
    detector.analyze(0, tone)
    steady = detector.analyze(500, tone)
    assert steady.onset is False
    assert steady.change_score < 0.2


def test_changes_below_silence_floor_do_not_trigger() -> None:
    detector = AudioChangeDetector()
    detector.analyze(0, np.zeros(8_000, dtype=np.int16))
    codec_noise = np.full(8_000, 8, dtype=np.int16)
    feature = detector.analyze(500, codec_noise)
    assert feature.active is False
    assert feature.change_score < 0.72


def test_audio_timeline_returns_latest_causal_window() -> None:
    detector = AudioChangeDetector()
    features = [detector.analyze(0, pcm_tone(0.1)), detector.analyze(500, pcm_tone(0.2))]
    timeline = AudioFeatureTimeline(features)
    assert timeline.at(499).timestamp_ms == 0
    assert timeline.at(500).timestamp_ms == 500


def test_invalid_audio_config_is_rejected() -> None:
    with pytest.raises(ValueError):
        AudioChangeConfig(window_ms=0)
