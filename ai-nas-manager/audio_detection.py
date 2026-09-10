"""Lightweight causal audio-change signals for fragment generation."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from math import log10
from pathlib import Path
from typing import Iterator


def _clip(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


@dataclass(frozen=True, slots=True)
class AudioFeature:
    timestamp_ms: int
    rms_dbfs: float
    peak: float
    zero_crossing_rate: float
    spectral_flux: float
    change_score: float
    active: bool
    onset: bool
    label: str

    def as_dict(self) -> dict[str, object]:
        return {
            "timestamp_ms": self.timestamp_ms,
            "rms_dbfs": round(self.rms_dbfs, 3),
            "peak": round(self.peak, 4),
            "zero_crossing_rate": round(self.zero_crossing_rate, 4),
            "spectral_flux": round(self.spectral_flux, 4),
            "change_score": round(self.change_score, 4),
            "active": self.active,
            "onset": self.onset,
            "label": self.label,
        }


@dataclass(frozen=True, slots=True)
class AudioChangeConfig:
    sample_rate: int = 16_000
    window_ms: int = 500
    silence_dbfs: float = -45.0
    onset_db: float = 9.0

    def __post_init__(self) -> None:
        if self.sample_rate <= 0 or self.window_ms <= 0 or self.onset_db <= 0:
            raise ValueError("audio sampling values must be positive")
        if self.silence_dbfs >= 0:
            raise ValueError("silence_dbfs must be negative")

    @property
    def samples_per_window(self) -> int:
        return round(self.sample_rate * self.window_ms / 1000)


class AudioChangeDetector:
    """Convert mono PCM windows into a normalized change score."""

    def __init__(self, config: AudioChangeConfig | None = None) -> None:
        self.config = config or AudioChangeConfig()
        self._previous_dbfs: float | None = None
        self._previous_active = False
        self._previous_spectrum = None

    def analyze(self, timestamp_ms: int, pcm: bytes | object) -> AudioFeature:
        import numpy as np

        samples = (
            np.frombuffer(pcm, dtype="<i2")
            if isinstance(pcm, bytes)
            else np.asarray(pcm, dtype=np.int16)
        )
        if samples.size == 0:
            raise ValueError("PCM window must not be empty")
        normalized = samples.astype(np.float32) / 32768.0
        rms = float(np.sqrt(np.mean(normalized * normalized)))
        rms_dbfs = 20.0 * log10(max(rms, 1e-6))
        peak = float(np.max(np.abs(normalized)))
        active = rms_dbfs >= self.config.silence_dbfs
        zero_crossing = float(np.mean(normalized[1:] * normalized[:-1] < 0)) if samples.size > 1 else 0.0

        windowed = normalized * np.hanning(samples.size)
        spectrum = np.abs(np.fft.rfft(windowed))
        total = float(spectrum.sum())
        spectrum = spectrum / total if total > 1e-9 else spectrum
        spectral_flux = (
            0.0
            if (
                not active
                or not self._previous_active
                or self._previous_spectrum is None
                or self._previous_spectrum.shape != spectrum.shape
            )
            else float(np.maximum(spectrum - self._previous_spectrum, 0).sum())
        )

        if self._previous_dbfs is None:
            rise_db = level_delta = 0.0
            transition = False
        else:
            rise_db = max(0.0, rms_dbfs - self._previous_dbfs)
            level_delta = abs(rms_dbfs - self._previous_dbfs)
            transition = active != self._previous_active
        onset_score = _clip(rise_db / (self.config.onset_db * 2.0)) if active else 0.0
        delta_score = _clip(level_delta / 30.0) if active else 0.0
        flux_score = _clip(spectral_flux * 5.0)
        change_score = max(
            onset_score,
            0.65 * delta_score,
            0.80 * flux_score,
            0.85 if active and transition else 0.60 if transition else 0.0,
        )
        onset = active and (rise_db >= self.config.onset_db or (transition and peak >= 0.08))
        if not active:
            label = "silence"
        elif onset and peak >= 0.65:
            label = "impact_candidate"
        elif 0.015 <= zero_crossing <= 0.30:
            label = "voice_or_tonal_activity"
        else:
            label = "audio_activity"

        self._previous_dbfs = rms_dbfs
        self._previous_active = active
        self._previous_spectrum = spectrum
        return AudioFeature(
            timestamp_ms, rms_dbfs, peak, zero_crossing, spectral_flux,
            _clip(change_score), active, onset, label,
        )


def iter_audio_features(
    source: Path,
    ffmpeg: str = "ffmpeg",
    config: AudioChangeConfig | None = None,
) -> Iterator[AudioFeature]:
    """Decode the first audio stream and yield fixed-duration causal windows."""

    settings = config or AudioChangeConfig()
    command = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-i", str(source),
        "-map", "0:a:0?", "-vn", "-sn", "-ac", "1", "-ar", str(settings.sample_rate),
        "-acodec", "pcm_s16le", "-f", "s16le", "pipe:1",
    ]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert process.stdout is not None
    detector = AudioChangeDetector(settings)
    byte_count = settings.samples_per_window * 2
    index = 0
    yielded = False
    try:
        while True:
            data = process.stdout.read(byte_count)
            if not data:
                break
            if len(data) < byte_count:
                data += bytes(byte_count - len(data))
            yielded = True
            yield detector.analyze(index * settings.window_ms, data)
            index += 1
    finally:
        process.stdout.close()
        return_code = process.wait()
        stderr = process.stderr.read().decode("utf-8", errors="replace") if process.stderr else ""
        missing_audio = (
            "does not contain any stream" in stderr
            or "matches no streams" in stderr
        )
        if return_code and not (not yielded and missing_audio):
            raise RuntimeError(f"ffmpeg audio decode failed: {stderr.strip()[-500:]}")


class AudioFeatureTimeline:
    """Lookup the latest causal audio window at a video timestamp."""

    def __init__(self, features: list[AudioFeature]) -> None:
        self.features = features
        self._index = 0

    def at(self, timestamp_ms: int) -> AudioFeature | None:
        if not self.features:
            return None
        while self._index + 1 < len(self.features) and self.features[self._index + 1].timestamp_ms <= timestamp_ms:
            self._index += 1
        feature = self.features[self._index]
        return feature if feature.timestamp_ms <= timestamp_ms else None
