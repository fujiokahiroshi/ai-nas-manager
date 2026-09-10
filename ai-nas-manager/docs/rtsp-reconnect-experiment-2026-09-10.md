# RTSP disconnect and reconnect experiment (2026-09-10)

## Outcome

The Windows input-layer experiment passed. A local MP4 was published as a
looping RTSP stream, the publisher was stopped at 7 seconds, and it was started
again after a requested 3-second outage.

| Measurement | Result |
|---|---:|
| Experiment duration | 20 s |
| Sampling rate | 2 fps |
| Sampled frames | 28 |
| Frames before interruption | 11 |
| Frames after reconnection | 17 |
| Successful connections | 2 |
| Detected disconnections | 1 |
| Disconnect-to-reconnect interval | 5,703 ms |

The interval includes the receiver's 2-second socket timeout, failed reconnect
attempts while the path did not exist, and arrival of the first complete frame.
It is therefore longer than the publisher's requested 3-second outage.

## Verified event behavior

- First usable frame and `CONNECTED`: 2,500 ms.
- `DISCONNECTED`: 6,734 ms.
- Reconnected on a complete decoded frame: 12,437 ms.
- The active fragment closed with `source_disconnected`.
- A new fragment ID opened with `stream_start` after reconnection.
- The pipeline timeline remained monotonic even though source timestamps may
  restart with the publisher.

The machine-readable trace is in
`docs/rtsp-reconnect-experiment-2026-09-10.json`.

## Architecture exercised

```text
MP4 -> FFmpeg publisher -> MediaMTX RTSP/TCP
                         -> reconnectable FFmpeg decoder (2 fps, 640x360 BGR)
                         -> YOLOX object signal
                         -> online fragmenter OPEN/UPDATE/CLOSE
```

FFmpeg is an input adapter, not part of the fragmentation protocol. On the NAS,
Rockchip MPP/RGA can replace the decoder and scaler while keeping the same frame
and connection-state events.

## Reproduction

Download MediaMTX v1.21.0 for Windows amd64 into
`tools/mediamtx/mediamtx.exe`, then run from this directory:

```powershell
uv run --with-requirements requirements-vision-experiment.txt python `
  scripts/run_rtsp_reconnect_experiment.py `
  media/test_dataset/pedestrian_area_1080p25.mp4 `
  --ffmpeg C:\path\to\ffmpeg.exe `
  --mediamtx tools/mediamtx/mediamtx.exe `
  --detector models/yolox_tiny.onnx `
  --duration 20 --interrupt-at 7 --outage 3 `
  --output docs/rtsp-reconnect-experiment-2026-09-10.json
```

The command returns a non-zero exit code unless there is a detected disconnect
and at least three decoded frames from two separate connections.

