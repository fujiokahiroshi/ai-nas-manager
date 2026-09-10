"""Generate a static-video sample containing two distinct audio events."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("media/test_dataset/audio_event_sample.mp4"),
    )
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    audio = (
        "aevalsrc=if(between(t\\,2\\,3)\\,0.25*sin(2*PI*440*t)\\,"
        "if(between(t\\,6\\,6.25)\\,0.85*sin(2*PI*1200*t)\\,0))"
        ":s=16000:d=10"
    )
    subprocess.run([
        args.ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "color=c=0x162033:s=640x360:r=25:d=10",
        "-f", "lavfi", "-i", audio,
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", str(args.output),
    ], check=True)
    print(args.output.resolve())


if __name__ == "__main__":
    main()

