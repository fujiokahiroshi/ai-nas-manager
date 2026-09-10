import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from build_fragment_gallery import render_gallery


def test_gallery_places_image_and_escaped_text_together() -> None:
    payload = {
        "source": "sample.mp4",
        "stream_wall_seconds": 10,
        "queue_replaced": 1,
        "inferences": [{
            "revision": 2,
            "source_timestamp_ms": 1500,
            "completed_wall_ms": 4000,
            "latency_ms": 2500,
            "completed_during_stream": True,
            "observation_ja": "人物 < 自転車",
            "objects": ["人物", "自転車"],
            "action_ja": "歩いている",
            "change_from_previous_ja": "人物が増えた",
            "confidence": 0.8,
        }],
    }
    page = render_gallery(payload, ["base64-image"])
    assert 'data:image/jpeg;base64,base64-image' in page
    assert "人物 &lt; 自転車" in page
    assert "映像中に生成" in page
