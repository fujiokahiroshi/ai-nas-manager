# Live Gemma experiment 2026-09-10

## Purpose

Verify the complete consumer-facing path while a real video is still playing:

```text
video -> object-assisted Fragment UPDATE -> latest queue -> Gemma -> Japanese text
```

## Conditions

- Input: `pedestrian_area_1080p25.mp4` (approximately 15 seconds)
- Fragment sampling: 2 fps, 320x180 luma
- Object detector: YOLOX-Tiny ONNX Runtime CPU, 416x416
- Gemma evidence: selected 640x360 JPEG, quality 82
- LM Studio model: `gemma4-12b-qat`
- Context: 16384, parallel: 1, reasoning: off
- Gemma output limit: 140 tokens
- Machine-readable result: `live-gemma-pedestrian-2026-09-10.json`

## Result

- Stream wall time: 14.945 seconds
- First text completion: 3.637 seconds after stream start
- Gemma results: 6
- Results completed while video was still playing: 4
- Mean Gemma latency: approximately 3.30 seconds
- Pending stale revisions replaced: 2
- Whole fragments dropped: 0
- Failures: 0

| Wall time | Evidence video time | Revision | Completed during stream |
|---:|---:|---:|:---:|
| 3.64 s | 0.00 s | 1 | yes |
| 6.80 s | 1.92 s | 2 | yes |
| 9.93 s | 5.76 s | 4 | yes |
| 13.30 s | 9.60 s | 6 | yes |
| 16.50 s | 11.52 s | 7 | no |
| 19.86 s | 13.44 s | 8 | no |

The first generated text reported multiple moving people and a person with a
bicycle.  Later revisions described pedestrians, storefronts, bicycles, and a
person carrying a large white bag.  The observations match visible evidence in
the sampled frames closely enough to justify continued system integration.

The video ended at about 15 seconds, so two queued results completed afterward.
On an indefinite live stream, the same worker continues publishing approximately
every 3-4 seconds.  It does not need an end-of-stream signal to publish text.

## Queue behavior

Fragment updates arrived faster than Gemma could process them.  The latest-only
queue replaced revisions 3 and 5 before inference.  This is intended behavior:
Gemma received current evidence rather than building an ever-growing backlog.

## Classification stability

Bicycle and motorcycle detections now share the trigger group `two_wheeler`.
This prevents detector class oscillation from creating false fragment changes.
Gemma is asked to distinguish the exact type only when the selected image is
clear.

## Conclusion

The root hypothesis is demonstrated on a real video: useful Japanese text is
generated and revised while the stream is active, without sending every frame
to Gemma.  The next layer is a Scene accumulator that combines Fragment text,
object tracks, and subtitle cues into a stable rolling description.

