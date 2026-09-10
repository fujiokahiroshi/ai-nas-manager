# Online multi-signal fragmentation v1

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

## Streaming state

- `OPEN`: emitted immediately at stream start, or when activity resumes.
- `UPDATE`: emitted at a bounded interval when meaningful evidence changes.
- `CLOSE`: emitted after quiet, a hard cut, maximum duration, or end-of-stream.
- hard cuts close the prior fragment and open the next one at the same frame;
- maximum duration rolls directly into a continuation fragment;
- only the three strongest representative timestamps are retained by default.

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
- Audio and broadcast subtitle extraction are input hooks, not decoders yet.
- Thresholds have only been exercised on four short test videos.  Human ground
  truth is required before calling them production-optimal.

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

