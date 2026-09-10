from live_semantics import FragmentEvidence, LatestEvidenceQueue, parse_json_message


def evidence(revision: int) -> FragmentEvidence:
    return FragmentEvidence("fragment-1", revision, revision * 1000, b"jpeg", {})


def test_latest_queue_replaces_stale_revision() -> None:
    queue = LatestEvidenceQueue()
    queue.put(evidence(1))
    queue.put(evidence(2))
    assert queue.replaced == 1
    assert queue.get().revision == 2
    queue.close()
    assert queue.get() is None


def test_parse_json_message_accepts_code_fence() -> None:
    assert parse_json_message('```json\n{"observation_ja":"人物"}\n```') == {
        "observation_ja": "人物"
    }
