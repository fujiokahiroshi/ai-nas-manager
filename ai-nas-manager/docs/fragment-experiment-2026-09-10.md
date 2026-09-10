# Fragment experiment 2026-09-10

## Conditions

- Analysis frame: 320x180 luma
- Sampling: 2 fps
- Algorithm: `online-multisignal-v1`
- Inputs: four downloaded real-video samples
- Object/audio/subtitle signals: disabled in this visual-only baseline
- Machine-readable result: `fragment-experiment-2026-09-10.json`

## Result

| Video | Sampled frames | Old scene cuts | Online fragments | Online updates |
|---|---:|---:|---:|---:|
| pedestrian_area_1080p25 | 30 | 0 | 1 | 1 |
| highway_cif | 133 | 0 | 4 | 4 |
| bus_cif | 10 | 0 | 1 | 0 |
| carphone_qcif | 25 | 0 | 2 | 2 |

The previous FFmpeg scene-cut detector found no boundaries in any of the four
videos at threshold 0.3.  It therefore cannot publish useful intermediate
events for these continuous shots.

The new fragmenter opens the initial fragment at time zero.  On the pedestrian
video it issued an update at 11.0 seconds and closed at end-of-stream (14.5 s).
On the highway video it found later adaptive activity regions even though there
were no edit cuts.  This demonstrates the required online behavior, but does
not yet prove semantic event recall.

## Interpretation

The experiment rejects hard-cut-only fragmentation for AI NAS.  Adaptive visual
signals solve the zero-output failure, while mandatory stream-start `OPEN`
ensures Gemma can produce initial text without waiting for a boundary.

The pedestrian update is still late for a consumer product.  The next controlled
variable must be RKNN object detection/tracking: person/bicycle appearance,
disappearance, count change, and track motion should become `object_change`.
That signal is expected to trigger updates earlier than whole-frame statistics.

## Acceptance criteria for the next experiment

- first fragment event within 500 ms of sampled input;
- person/bicycle important-event recall at least 90%;
- false fragment boundaries at most one per minute on a static scene;
- first Gemma text within 5 seconds;
- no unbounded inference queue growth;
- fewer than one Gemma request per second on ordinary footage.

