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


def test_play_with_seek_seconds_sets_seek_to() -> None:
    module._apply_play(
        "file", "\\\\wsl.localhost\\Ubuntu\\tmp\\demo.mp4", 3, "CH3", tag=None, seek_seconds=12.5
    )
    assert module._state["seek_to"] == 12.5


def test_stop_clears_seek_to() -> None:
    module._state["seek_to"] = 12.5
    module._apply_stop()
    assert module._state["seek_to"] is None


def test_apply_seek_updates_state_without_changing_command() -> None:
    module._state["command"] = "play"
    module._state["seq"] = 0

    module._apply_seek(30.0)

    assert module._state["seek_to"] == 30.0
    assert module._state["command"] == "play"
    assert module._state["seq"] == 1


def test_play_with_thumbnail_path_sets_thumbnail_uri() -> None:
    module._apply_play(
        "file",
        "\\\\wsl.localhost\\Ubuntu\\tmp\\demo.mp4",
        3,
        "CH3",
        thumbnail_path="\\\\wsl.localhost\\Ubuntu\\tmp\\thumb.png",
    )
    assert module._state["thumbnail"] == "file://wsl.localhost/Ubuntu/tmp/thumb.png"


def test_play_without_thumbnail_path_clears_thumbnail() -> None:
    module._state["thumbnail"] = "file://wsl.localhost/Ubuntu/tmp/old.png"
    module._apply_play("file", "\\\\wsl.localhost\\Ubuntu\\tmp\\demo.mp4", 3, "CH3")
    assert module._state["thumbnail"] is None


def test_render_choices_then_get_selection_before_click() -> None:
    module._apply_render_choices(
        [
            {
                "thumbnail_path": "\\\\wsl.localhost\\Ubuntu\\tmp\\a.png",
                "label": "候補A",
                "source_value": "\\\\wsl.localhost\\Ubuntu\\tmp\\a.mp4",
            },
            {
                "thumbnail_path": "\\\\wsl.localhost\\Ubuntu\\tmp\\b.png",
                "label": "候補B",
                "source_value": "\\\\wsl.localhost\\Ubuntu\\tmp\\b.mp4",
                "tag": "説明B",
                "seek_seconds": 4.0,
            },
        ]
    )
    assert module._get_selection() == {"selected": None}
    assert len(module._choice_state["options"]) == 2
    assert module._choice_state["options"][0]["thumbnail_uri"] == "file://wsl.localhost/Ubuntu/tmp/a.png"


def test_apply_choice_sets_selection() -> None:
    module._apply_render_choices(
        [
            {
                "thumbnail_path": "\\\\wsl.localhost\\Ubuntu\\tmp\\a.png",
                "label": "候補A",
                "source_value": "\\\\wsl.localhost\\Ubuntu\\tmp\\a.mp4",
            },
            {
                "thumbnail_path": "\\\\wsl.localhost\\Ubuntu\\tmp\\b.png",
                "label": "候補B",
                "source_value": "\\\\wsl.localhost\\Ubuntu\\tmp\\b.mp4",
                "tag": "説明B",
                "seek_seconds": 4.0,
            },
        ]
    )
    module._apply_choice(1)
    result = module._get_selection()
    assert result["selected"]["index"] == 1
    assert result["selected"]["label"] == "候補B"
    assert result["selected"]["source_value"] == "\\\\wsl.localhost\\Ubuntu\\tmp\\b.mp4"
    assert result["selected"]["seek_seconds"] == 4.0
    assert "thumbnail_uri" not in result["selected"]


def test_apply_choice_rejects_out_of_range_index() -> None:
    module._apply_render_choices(
        [
            {
                "thumbnail_path": "\\\\wsl.localhost\\Ubuntu\\tmp\\a.png",
                "label": "候補A",
                "source_value": "\\\\wsl.localhost\\Ubuntu\\tmp\\a.mp4",
            }
        ]
    )
    message = module._apply_choice(5)
    assert "無効" in message
    assert module._get_selection() == {"selected": None}


def test_render_choices_requires_thumbnail_and_source() -> None:
    message = module._apply_render_choices([{"label": "抜けあり"}])
    assert "thumbnail_path" in message


def test_get_status_returns_current_state() -> None:
    module._apply_play(
        "file", "\\\\wsl.localhost\\Ubuntu\\tmp\\demo.mp4", 2, "CH2", tag="ダミーtag"
    )

    status = module._get_status()

    assert status["channel"] == 2
    assert status["title"] == "CH2"
    assert status["tag"] == "ダミーtag"
    assert status["command"] == "play"
