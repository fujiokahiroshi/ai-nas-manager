"""Optional object detection and temporal object-state change adapters.

YOLOX/ONNX Runtime is used only for the Windows experiment.  The production
RK3588 adapter should return the same normalized ``Detection`` records from an
RKNN model, leaving the fragment algorithm unchanged.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


COCO_CLASSES = (
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck",
    "boat", "traffic light", "fire hydrant", "stop sign", "parking meter", "bench",
    "bird", "cat", "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra",
    "giraffe", "backpack", "umbrella", "handbag", "tie", "suitcase", "frisbee",
    "skis", "snowboard", "sports ball", "kite", "baseball bat", "baseball glove",
    "skateboard", "surfboard", "tennis racket", "bottle", "wine glass", "cup",
    "fork", "knife", "spoon", "bowl", "banana", "apple", "sandwich", "orange",
    "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair", "couch",
    "potted plant", "bed", "dining table", "toilet", "tv", "laptop", "mouse",
    "remote", "keyboard", "cell phone", "microwave", "oven", "toaster", "sink",
    "refrigerator", "book", "clock", "vase", "scissors", "teddy bear", "hair drier",
    "toothbrush",
)


@dataclass(frozen=True, slots=True)
class Detection:
    class_id: int
    confidence: float
    x1: float
    y1: float
    x2: float
    y2: float

    @property
    def label(self) -> str:
        return COCO_CLASSES[self.class_id] if 0 <= self.class_id < len(COCO_CLASSES) else str(self.class_id)

    def as_dict(self) -> dict[str, object]:
        return {
            "class_id": self.class_id,
            "label": self.label,
            "confidence": round(self.confidence, 4),
            "box": [round(value, 4) for value in (self.x1, self.y1, self.x2, self.y2)],
        }


def intersection_over_union(left: Detection, right: Detection) -> float:
    if left.class_id != right.class_id:
        return 0.0
    x1, y1 = max(left.x1, right.x1), max(left.y1, right.y1)
    x2, y2 = min(left.x2, right.x2), min(left.y2, right.y2)
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    left_area = max(0.0, left.x2 - left.x1) * max(0.0, left.y2 - left.y1)
    right_area = max(0.0, right.x2 - right.x1) * max(0.0, right.y2 - right.y1)
    union = left_area + right_area - intersection
    return intersection / union if union else 0.0


def object_change_score(previous: Iterable[Detection], current: Iterable[Detection]) -> float:
    """Measure count, entry/exit, and position changes without persistent IDs."""

    old, new = list(previous), list(current)
    if not old and not new:
        return 0.0
    old_counts = Counter(item.class_id for item in old)
    new_counts = Counter(item.class_id for item in new)
    classes = old_counts.keys() | new_counts.keys()
    count_delta = sum(abs(old_counts[key] - new_counts[key]) for key in classes)
    count_scale = max(1, sum(max(old_counts[key], new_counts[key]) for key in classes))
    count_score = min(1.0, count_delta / count_scale)

    unmatched_old = set(range(len(old)))
    matched_ious: list[float] = []
    for detection in new:
        candidates = [(intersection_over_union(old[index], detection), index) for index in unmatched_old]
        best_iou, best_index = max(candidates, default=(0.0, -1))
        if best_iou >= 0.15:
            unmatched_old.remove(best_index)
            matched_ious.append(best_iou)
    unmatched = len(unmatched_old) + max(0, len(new) - len(matched_ious))
    churn_score = min(1.0, unmatched / max(1, len(old) + len(new)))
    movement_score = (
        sum(1.0 - value for value in matched_ious) / len(matched_ious)
        if matched_ious else (1.0 if old or new else 0.0)
    )
    return min(1.0, 0.50 * count_score + 0.30 * churn_score + 0.20 * movement_score)


class YoloXOnnxDetector:
    """Small ONNX Runtime detector compatible with official YOLOX exports."""

    def __init__(
        self,
        model_path: str | Path,
        *,
        input_size: int = 416,
        score_threshold: float = 0.25,
        nms_threshold: float = 0.45,
        class_ids: set[int] | None = None,
    ) -> None:
        try:
            import onnxruntime as ort
        except ImportError as exc:
            raise RuntimeError("install requirements-vision-experiment.txt") from exc
        self.input_size = input_size
        self.score_threshold = score_threshold
        self.nms_threshold = nms_threshold
        self.class_ids = class_ids
        self.session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
        self.input_name = self.session.get_inputs()[0].name

    @staticmethod
    def _nms(boxes, scores, threshold: float):
        import numpy as np

        order = scores.argsort()[::-1]
        keep: list[int] = []
        while order.size:
            index = int(order[0])
            keep.append(index)
            if order.size == 1:
                break
            rest = order[1:]
            xx1 = np.maximum(boxes[index, 0], boxes[rest, 0])
            yy1 = np.maximum(boxes[index, 1], boxes[rest, 1])
            xx2 = np.minimum(boxes[index, 2], boxes[rest, 2])
            yy2 = np.minimum(boxes[index, 3], boxes[rest, 3])
            intersection = np.maximum(0.0, xx2 - xx1) * np.maximum(0.0, yy2 - yy1)
            area_index = max(0.0, boxes[index, 2] - boxes[index, 0]) * max(0.0, boxes[index, 3] - boxes[index, 1])
            areas_rest = np.maximum(0.0, boxes[rest, 2] - boxes[rest, 0]) * np.maximum(0.0, boxes[rest, 3] - boxes[rest, 1])
            overlap = intersection / np.maximum(area_index + areas_rest - intersection, 1e-9)
            order = rest[overlap <= threshold]
        return keep

    def detect(self, bgr_image) -> list[Detection]:
        import cv2
        import numpy as np

        source_height, source_width = bgr_image.shape[:2]
        ratio = min(self.input_size / source_height, self.input_size / source_width)
        resized = cv2.resize(
            bgr_image,
            (int(source_width * ratio), int(source_height * ratio)),
            interpolation=cv2.INTER_LINEAR,
        )
        padded = np.full((self.input_size, self.input_size, 3), 114, dtype=np.uint8)
        padded[: resized.shape[0], : resized.shape[1]] = resized
        tensor = np.ascontiguousarray(padded.transpose(2, 0, 1), dtype=np.float32)[None]
        output = self.session.run(None, {self.input_name: tensor})[0]

        strides = (8, 16, 32)
        grids, expanded = [], []
        for stride in strides:
            size = self.input_size // stride
            x, y = np.meshgrid(np.arange(size), np.arange(size))
            grid = np.stack((x, y), axis=2).reshape(1, -1, 2)
            grids.append(grid)
            expanded.append(np.full((*grid.shape[:2], 1), stride))
        output[..., :2] = (output[..., :2] + np.concatenate(grids, axis=1)) * np.concatenate(expanded, axis=1)
        output[..., 2:4] = np.exp(output[..., 2:4]) * np.concatenate(expanded, axis=1)
        prediction = output[0]
        boxes = prediction[:, :4]
        class_scores = prediction[:, 4:5] * prediction[:, 5:]
        class_ids = class_scores.argmax(axis=1)
        scores = class_scores[np.arange(len(class_ids)), class_ids]
        valid = scores >= self.score_threshold
        if self.class_ids is not None:
            valid &= np.isin(class_ids, list(self.class_ids))
        boxes, scores, class_ids = boxes[valid], scores[valid], class_ids[valid]
        if not len(boxes):
            return []

        xyxy = np.empty_like(boxes)
        xyxy[:, 0] = (boxes[:, 0] - boxes[:, 2] / 2) / ratio
        xyxy[:, 1] = (boxes[:, 1] - boxes[:, 3] / 2) / ratio
        xyxy[:, 2] = (boxes[:, 0] + boxes[:, 2] / 2) / ratio
        xyxy[:, 3] = (boxes[:, 1] + boxes[:, 3] / 2) / ratio
        selected: list[int] = []
        for class_id in set(int(value) for value in class_ids):
            indexes = np.flatnonzero(class_ids == class_id)
            selected.extend(int(indexes[item]) for item in self._nms(xyxy[indexes], scores[indexes], self.nms_threshold))
        detections = []
        for index in selected:
            x1, y1, x2, y2 = xyxy[index]
            detections.append(
                Detection(
                    int(class_ids[index]), float(scores[index]),
                    max(0.0, float(x1 / source_width)), max(0.0, float(y1 / source_height)),
                    min(1.0, float(x2 / source_width)), min(1.0, float(y2 / source_height)),
                )
            )
        return sorted(detections, key=lambda item: item.confidence, reverse=True)
