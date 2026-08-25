import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("media_renderer_server", Path(__file__).with_name("server.py"))
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(module)


def test_browser_pause_reports_stop_state() -> None:
    module._state["command"] = "play"
    module._state["seq"] = 0

    module._apply_browser_report("stop")

    assert module._state["command"] == "stop"
    assert module._state["seq"] == 1


def test_play_and_stop_updates_state_sequence() -> None:
    module._state["command"] = "stop"
    module._state["seq"] = 5

    module._apply_play("file", "\\\\wsl.localhost\\Ubuntu\\tmp\\demo.mp4", 3, "CH3")
    assert module._state["command"] == "play"
    assert module._state["seq"] == 6

    module._apply_stop()
    assert module._state["command"] == "stop"
    assert module._state["seq"] == 7
