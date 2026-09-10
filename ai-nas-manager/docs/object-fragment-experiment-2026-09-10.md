# Object-assisted fragment experiment 2026-09-10

## Purpose

Test whether object-state changes can update a fragment earlier than whole-frame
visual statistics on `pedestrian_area_1080p25.mp4`.

## Conditions

- Video: 1920x1080, 25 fps, approximately 15 seconds
- Sampling target: 2 fps (32 analyzed frames)
- Detector: official YOLOX-Tiny ONNX, 416x416, ONNX Runtime CPU
- Relevant COCO classes: person, bicycle, car, motorcycle, bus, truck
- Fragment visual frame: 320x180 luma
- Machine-readable result: `object-fragment-experiment-2026-09-10.json`

The ONNX file is intentionally not committed.  See `models/README.md` for its
source and SHA-256 digest.

## Result

| Measurement | Visual-only v1 | Object-assisted v1 |
|---|---:|---:|
| First OPEN | 0.00 s | 0.00 s |
| First UPDATE | 11.00 s | 1.92 s |
| UPDATE count | 1 | 7 |
| CLOSE | 14.50 s | 14.88 s |

Object-assisted updates occurred at 1.92, 3.84, 5.76, 7.68, 9.60, 11.52,
and 13.44 seconds.  This is an online cadence: no decision waited for the video
to end.

The CPU detector averaged 26.554 ms per analyzed image.  Processing all 32
samples, including video decode and fragmentation, took 2.846 seconds.  At a
2 fps sampling rate the detector itself consumes about 53 ms of CPU time per
stream-second on this Windows test machine.

The detector found between 5 and 14 people in every sample.  It detected a
bicycle at many timestamps, including 0.0, 1.92, 2.88, 5.28, 5.76, 6.24,
7.20-10.56, and 12.0-14.88 seconds.  One frame classified a two-wheeler as a
motorcycle.  This class instability should be normalized to a consumer-facing
`two_wheeler` group for fragment triggering; Gemma can make the later semantic
distinction using the selected colour evidence.

## Conclusion

Object state fixes the late-update failure of visual-only fragmentation.  It is
fast enough to run before Gemma and can be ported to RKNN.  The seven update
events are faster than the measured 3-4 second Gemma latency, so the existing
latest-revision queue must coalesce intermediate updates.  A product default of
roughly one Gemma request every 3 seconds is appropriate while the fragmenter
continues observing at 2 fps.

This experiment measures responsiveness, not final recall/precision.  The next
step is a small hand-labelled timeline for person and two-wheeler entry/exit,
followed by threshold selection against that ground truth.

