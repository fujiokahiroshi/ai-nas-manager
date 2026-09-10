"""Normalize public and user-recorded datasets into one evaluation manifest."""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
from math import ceil
from collections import defaultdict
from pathlib import Path


def duration_ms(value: str) -> int:
    minutes, seconds = (int(part) for part in value.split(":"))
    return (minutes * 60 + seconds) * 1_000


def tvsum_items(args: argparse.Namespace) -> list[dict[str, object]]:
    with args.info.open(encoding="utf-8", newline="") as source:
        info = {row["video_id"]: row for row in csv.DictReader(source, delimiter="\t")}
    ratings: dict[str, list[list[int]]] = defaultdict(list)
    with args.annotations.open(encoding="utf-8", newline="") as source:
        for row in csv.reader(source, delimiter="\t"):
            ratings[row[0]].append([int(value) for value in row[2].split(",")])
    items = []
    for video_id in args.ids:
        video = args.video_dir / f"{video_id}.mp4"
        if not video.exists():
            raise FileNotFoundError(video)
        judges = ratings[video_id]
        if len(judges) != 20:
            raise ValueError(f"{video_id}: expected 20 TVSum judges, got {len(judges)}")
        video_duration_ms = duration_ms(info[video_id]["length"])
        shot_count = ceil(video_duration_ms / 2_000)
        # The TSV calls these shot ratings, but stores a frame-length vector
        # with each two-second rating repeated. Sampling at each interval's
        # center avoids assuming a fixed source FPS.
        def interval_rating(judge: list[int], index: int) -> int:
            center_ms = min(video_duration_ms - 1, index * 2_000 + 1_000)
            frame_index = min(len(judge) - 1, int(center_ms * len(judge) / video_duration_ms))
            return judge[frame_index]
        importance = [
            {
                "start_ms": index * 2_000,
                "end_ms": min((index + 1) * 2_000, video_duration_ms),
                "score": round(sum(interval_rating(judge, index) for judge in judges) / len(judges), 3),
            }
            for index in range(shot_count)
        ]
        items.append({
            "id": f"tvsum:{video_id}",
            "dataset": "TVSum",
            "video": str(video.resolve()),
            "duration_ms": video_duration_ms,
            "tasks": ["importance"],
            "boundaries_ms": [],
            "importance": importance,
            "metadata": {
                "category": info[video_id]["category"],
                "title": info[video_id]["title"],
                "judges": len(judges),
                "interval_ms": 2_000,
            },
        })
    return items


def probe_video(video: Path, ffprobe: str) -> tuple[float, int, int]:
    command = [
        ffprobe, "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=avg_frame_rate,nb_frames:format=duration", "-of", "json", str(video),
    ]
    payload = json.loads(subprocess.check_output(command, text=True, encoding="utf-8"))
    numerator, denominator = payload["streams"][0]["avg_frame_rate"].split("/")
    fps = int(numerator) / int(denominator)
    measured_duration = round(float(payload["format"]["duration"]) * 1_000)
    frame_text = payload["streams"][0].get("nb_frames")
    frame_count = int(frame_text) if frame_text and frame_text != "N/A" else round(measured_duration * fps / 1_000)
    return fps, measured_duration, frame_count


def clipshots_items(args: argparse.Namespace) -> list[dict[str, object]]:
    annotations = json.loads(args.annotations.read_text(encoding="utf-8"))
    videos = sorted(args.video_dir.glob("*.mp4"))
    items = []
    for video in videos:
        if video.name not in annotations:
            continue
        fps, measured_duration, frame_count = probe_video(video, args.ffprobe)
        annotated_frames = round(float(annotations[video.name]["frame_num"]))
        if abs(frame_count - annotated_frames) > args.frame_tolerance:
            print(
                f"Skip {video.name}: current frames={frame_count}, annotated={annotated_frames}",
            )
            continue
        boundaries = [
            [round(start * 1_000 / fps), round(end * 1_000 / fps)]
            for start, end in annotations[video.name]["transitions"]
        ]
        items.append({
            "id": f"clipshots:{video.stem}",
            "dataset": "ClipShots",
            "video": str(video.resolve()),
            "duration_ms": measured_duration,
            "tasks": ["boundary"],
            "boundaries_ms": boundaries,
            "importance": [],
            "metadata": {"fps": fps, "annotated_frames": annotated_frames, "measured_frames": frame_count},
        })
        if len(items) >= args.limit:
            break
    return items


def summe_items(args: argparse.Namespace) -> list[dict[str, object]]:
    annotations = json.loads(args.annotations.read_text(encoding="utf-8"))
    wanted = {name.casefold() for name in args.names}
    selected = {
        Path(entry["video"]).stem.casefold(): entry
        for entry in annotations
        if entry.get("source") == "summe"
        and entry.get("task") == "evs"
        and Path(entry["video"]).stem.casefold() in wanted
    }
    missing = sorted(wanted - selected.keys())
    if missing:
        raise ValueError(f"SumMe annotations not found: {', '.join(missing)}")
    items = []
    for name in args.names:
        entry = selected[name.casefold()]
        video = args.video_dir / f"{Path(entry['video']).stem}.mp4"
        if not video.exists():
            raise FileNotFoundError(video)
        _, measured_duration, _ = probe_video(video, args.ffprobe)
        annotated_duration = round(float(entry["duration"]) * 1_000)
        if abs(measured_duration - annotated_duration) > args.duration_tolerance_ms:
            raise ValueError(
                f"{video.name}: current duration={measured_duration} ms, "
                f"annotated={annotated_duration} ms"
            )
        importance = [
            {
                "start_ms": round(float(start) * 1_000),
                "end_ms": round(float(end) * 1_000),
                "score": 1,
            }
            for start, end in entry["tgt"]
        ]
        items.append({
            "id": f"summe:{video.stem}",
            "dataset": "SumMe",
            "video": str(video.resolve()),
            "duration_ms": measured_duration,
            "tasks": ["importance"],
            "boundaries_ms": [],
            "importance": importance,
            "importance_selection": "all",
            "metadata": {
                "annotation_source": "ETBench evs derived from SumMe",
                "annotation_index": entry["idx"],
                "annotated_duration_ms": annotated_duration,
            },
        })
    return items


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=Path, help="Existing manifest to extend")
    parser.add_argument("--output", type=Path, required=True)
    subparsers = parser.add_subparsers(dest="dataset", required=True)

    tvsum = subparsers.add_parser("tvsum")
    tvsum.add_argument("--info", type=Path, required=True)
    tvsum.add_argument("--annotations", type=Path, required=True)
    tvsum.add_argument("--video-dir", type=Path, required=True)
    tvsum.add_argument("--ids", nargs="+", required=True)

    clipshots = subparsers.add_parser("clipshots")
    clipshots.add_argument("--annotations", type=Path, required=True)
    clipshots.add_argument("--video-dir", type=Path, required=True)
    clipshots.add_argument("--limit", type=int, default=20)
    clipshots.add_argument("--frame-tolerance", type=int, default=2)
    clipshots.add_argument("--ffprobe", default="ffprobe")

    summe = subparsers.add_parser("summe")
    summe.add_argument("--annotations", type=Path, required=True)
    summe.add_argument("--video-dir", type=Path, required=True)
    summe.add_argument("--names", nargs="+", required=True)
    summe.add_argument("--duration-tolerance-ms", type=int, default=1_000)
    summe.add_argument("--ffprobe", default="ffprobe")
    args = parser.parse_args()

    payload = json.loads(args.base.read_text(encoding="utf-8")) if args.base else {"version": 1, "items": []}
    generators = {"tvsum": tvsum_items, "clipshots": clipshots_items, "summe": summe_items}
    generated = generators[args.dataset](args)
    existing = {item["id"] for item in payload["items"]}
    payload["items"].extend(item for item in generated if item["id"] not in existing)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Added {len(generated)} {args.dataset} items; total={len(payload['items'])}")
    print(args.output.resolve())


if __name__ == "__main__":
    main()
