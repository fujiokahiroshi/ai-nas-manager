# Online multi-signal fragmentation v1/v2

## Purpose

Create and revise semantic video fragments while a stream is still running.
The fragmenter must not wait for end-of-file and must not send every decoded
frame to the vision-language model.

## Pipeline

```text
encoded stream
  -> hardware decoder (Windows CUDA experiment / product Rockchip MPP)
  -> RGA/downscale to 320x180 luma at 2 fps
  -> visual signals + object/audio/subtitle signals
  -> adaptive online fragmenter
  -> OPEN / UPDATE / CLOSE events
  -> selected 640x360 colour evidence -> Gemma board
```

The boundary detector and Gemma are independent.  A future model can replace
Gemma without changing fragment identity or event storage.

## Signals

The dependency-free implementation in `online_fragmentation.py` calculates:

- sampled mean absolute luma difference;
- 16-bin luma histogram distance;
- spatial gradient (edge) change;
- ratio of pixels whose luma changed by at least 20;
- EWMA-normalized novelty against the recent stream baseline.

It also accepts normalized `object_change` and `audio_change` scores and a
subtitle string.  On RK3588, object changes are intended to come from a small
RKNN detector/tracker.  Subtitle changes can come from ARIB captions; audio
changes can come from VAD and speaker-change detection.

The initial visual score is:

```text
visual = 0.30*luma + 0.30*histogram + 0.15*edge + 0.25*changed_ratio
composite = 0.50*max(visual, adaptive_novelty)
          + 0.25*object_change
          + 0.10*audio_change
          + 0.15*subtitle_change
```

Weights and thresholds are configuration, not protocol constants.
In addition, an `audio_change` score of 0.72 or higher is an explicit event
trigger. This allows a sound onset to open a Fragment when the image is static.

## Streaming state

- `OPEN`: emitted immediately at stream start, or when activity resumes.
- `UPDATE`: emitted at a bounded interval when meaningful evidence changes.
- `CLOSE`: emitted after quiet, a hard cut, maximum duration, or end-of-stream.
- hard cuts close the prior fragment and open the next one at the same frame;
- maximum duration rolls directly into a continuation fragment;
- only the three strongest representative timestamps are retained by default.

## Fused v2 profiles

`run_fragment_experiment.py --profile fused-v2-balanced` enables three controls
that are intentionally absent from the legacy baseline:

- hysteresis: a visual, object, or audio signal must fall below its release
  threshold before the same sustained condition can fire again;
- signal-specific cooldowns: visual, object, audio, subtitle, and fused events
  have independent minimum intervals;
- evidence fusion: moderate changes require at least two simultaneous signals.
  A hard cut, new subtitle, high-confidence impact candidate, or very strong
  visual change can still fire alone.

Audio classification uses RMS, peak, zero-crossing rate, spectral flux, crest
factor, and spectral flatness. It distinguishes impact candidates from loud
steady voice/tonal activity; it is a lightweight candidate classifier, not a
general sound-recognition model.

Candidate generation and permanent storage are separate. `fragment_admission.py`
combines Gemma confidence with non-empty structured observations and trusted
trigger evidence. Manual markers are always confirmed. The PC app stores only
confirmed results by default; `--include-unconfirmed` is available for detector
development and review.

This design supplies early text while bounding Gemma calls.  Downstream queues
should coalesce stale revisions of the same fragment.

## Hardware boundary

`GrayFrame` is the adapter boundary.  The Windows experiment obtains it from
FFmpeg.  The NAS product should produce the same message from Rockchip MPP and
RGA without transferring full-resolution video through Python.

## Known limits of v1

- No object detector/tracker is wired in yet; a visually small person may not
  generate a strong update.
- Global camera motion is not compensated and can look like scene activity.
- Audio DSP and file decoding are implemented; live RTSP audio synchronization,
  sound classification, speech recognition, and broadcast subtitle decoding
  remain adapter work.
- Thresholds have only been exercised on four short test videos.  Human ground
  truth is required before calling them production-optimal.

## Object-assisted experiment

`object_detection.py` defines normalized detections, object-state comparison,
and a Windows YOLOX/ONNX Runtime adapter.  Its output enters the existing
`object_change` input; it does not couple the fragmenter to ONNX.  On RK3588 the
adapter can therefore be replaced with RKNN while preserving event behavior.

See `object-fragment-experiment-2026-09-10.md` for measured results.  On the
pedestrian sample, object changes reduced the first UPDATE time from 11.00 s to
1.92 s.

## Live Gemma result

`live_semantics.py` supplies a bounded latest-revision queue and LM Studio vision
adapter.  The real-time pedestrian experiment produced its first Japanese text
at 3.64 seconds and four text results before the 14.95-second stream ended.  Two
stale revisions were replaced, no fragment was dropped, and no request failed.
See `live-gemma-experiment-2026-09-10.md`.

## Audio-assisted result

`audio_detection.py` converts 16 kHz mono PCM into causal 500 ms change scores
using RMS, peak, zero-crossing rate, spectral flux, and onset transitions. In a
static-video comparison, visual-only analysis produced one Fragment and no
updates. Enabling audio produced two Fragments and an audio-driven update; the
second Fragment opened solely from an impact-like sound at 6.0 seconds. See
`audio-fragment-experiment-2026-09-10.md`.

## RTSP reconnect result

`stream_input.py` now provides an FFmpeg-based RTSP input adapter. It emits
explicit connection state changes, samples decoded video without waiting for
the stream to finish, and maintains a local monotonic timeline across source
timestamp resets. A disconnect closes the active fragment; the first complete
frame after recovery opens a new fragment ID.

The Windows test stopped a looping RTSP publisher at 7 seconds and restarted it
after 3 seconds. It decoded 11 frames before the interruption and 17 afterward,
with one disconnect and two usable connections. See
`rtsp-reconnect-experiment-2026-09-10.md`.

## Reproduction

From the `ai-nas-manager` directory:

```powershell
uv run --with pytest pytest tests/test_online_fragmentation.py -q
uv run python scripts/run_fragment_experiment.py `
  media/test_dataset/pedestrian_area_1080p25.mp4 `
  media/test_dataset/highway_cif.mp4 `
  media/test_dataset/bus_cif.mp4 `
  media/test_dataset/carphone_qcif.mp4 `
  --ffmpeg C:\path\to\ffmpeg.exe `
  --output docs/fragment-experiment-2026-09-10.json
```
