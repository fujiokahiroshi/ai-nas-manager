from object_detection import Detection, intersection_over_union, object_change_score


def detection(class_id: int = 0, x: float = 0.1) -> Detection:
    return Detection(class_id, 0.9, x, 0.1, x + 0.2, 0.5)


def test_iou_requires_same_class() -> None:
    assert intersection_over_union(detection(0), detection(1)) == 0.0
    assert intersection_over_union(detection(), detection()) == 1.0


def test_no_objects_has_no_change() -> None:
    assert object_change_score([], []) == 0.0


def test_object_entry_is_maximum_change() -> None:
    assert object_change_score([], [detection()]) == 1.0


def test_position_change_has_nonzero_score() -> None:
    score = object_change_score([detection(x=0.1)], [detection(x=0.2)])
    assert 0.0 < score < 1.0


def test_bicycle_motorcycle_class_flip_is_same_trigger_group() -> None:
    bicycle = detection(class_id=1)
    motorcycle = detection(class_id=3)
    assert bicycle.trigger_group == motorcycle.trigger_group == "two_wheeler"
    assert object_change_score([bicycle], [motorcycle]) == 0.0
