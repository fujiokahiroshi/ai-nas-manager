from discovery import discover_tuners


def test_discover_tuners_finds_running_virtual_tuner(virtual_tuner_process) -> None:
    tuners = discover_tuners(timeout_sec=4.0)
    names = [t.name for t in tuners]
    assert "test-tuner" in names

    found = next(t for t in tuners if t.name == "test-tuner")
    assert found.port == 8766
    assert found.host  # 何らかのIPが取れていること
