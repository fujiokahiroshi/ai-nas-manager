import importlib.util
import shutil
import tempfile
from pathlib import Path

spec = importlib.util.spec_from_file_location("media_renderer_server", Path(__file__).with_name("server.py"))
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(module)

# _apply_play/_apply_render_choices/_apply_render_picture open a real browser tab when
# no tab has polled recently (need_new_tab判定)。単体テストではブラウザを実際に
# 開かせたくないので無効化する(でないとテスト実行の度にタブが増殖してしまう)。
module.webbrowser.open = lambda *args, **kwargs: None
_real_open_in_browser = module._open_in_browser
module._open_in_browser = lambda *args, **kwargs: None

# _wait_for_ackは実際にブラウザがポーリングしてくるのを待つが、単体テストでは
# 誰も実際にポーリングしないため既定で常に成功したものとして扱う。「応答が
# 確認できない」失敗パスそのものをテストしたい場合だけ、個別に上書きする。
_real_wait_for_ack = module._wait_for_ack
module._wait_for_ack = lambda *args, **kwargs: True


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


def test_render_choices_reuses_tab_when_recently_polled() -> None:
    opts = [
        {
            "thumbnail_path": "\\\\wsl.localhost\\Ubuntu\\tmp\\a.png",
            "label": "候補A",
            "source_value": "\\\\wsl.localhost\\Ubuntu\\tmp\\a.mp4",
        }
    ]
    opened = []
    module._open_in_browser = lambda *a, **kw: opened.append(a)
    try:
        module._apply_render_choices(opts)
        assert len(opened) == 1  # 初回は新規タブ

        module._choice_last_seen = module.time.time()  # ポーリング中とみなす
        message = module._apply_render_choices(opts)
        assert len(opened) == 1  # 生存中は新規タブを開かない
        assert "既存のタブ" in message
    finally:
        module._open_in_browser = lambda *args, **kwargs: None


def test_render_choices_opens_new_tab_when_stale() -> None:
    opts = [
        {
            "thumbnail_path": "\\\\wsl.localhost\\Ubuntu\\tmp\\a.png",
            "label": "候補A",
            "source_value": "\\\\wsl.localhost\\Ubuntu\\tmp\\a.mp4",
        }
    ]
    opened = []
    module._open_in_browser = lambda *a, **kw: opened.append(a)
    try:
        module._choice_last_seen = module.time.time() - 999  # ポーリングが止まって久しい
        module._apply_render_choices(opts)
        assert len(opened) == 1
    finally:
        module._open_in_browser = lambda *args, **kwargs: None


def test_render_picture_reuses_tab_when_recently_polled() -> None:
    opened = []
    module._open_in_browser = lambda *a, **kw: opened.append(a)
    try:
        module._apply_render_picture("\\\\wsl.localhost\\Ubuntu\\tmp\\pic.png")
        assert len(opened) == 1

        module._picture_last_seen = module.time.time()
        message = module._apply_render_picture("\\\\wsl.localhost\\Ubuntu\\tmp\\pic2.png")
        assert len(opened) == 1
        assert "切り替えました" in message
        assert module._picture_state["picture_uri"] == "file://wsl.localhost/Ubuntu/tmp/pic2.png"
    finally:
        module._open_in_browser = lambda *args, **kwargs: None


def test_apply_play_reports_failure_when_no_ack_on_new_tab() -> None:
    module._last_seen = None
    module._wait_for_ack = lambda *a, **kw: False
    try:
        message = module._apply_play("file", "\\\\wsl.localhost\\Ubuntu\\tmp\\demo.mp4", 3, "CH3")
        assert "応答が確認できません" in message
        assert "初回のみ" not in message  # 成功メッセージを誤って返していないこと
    finally:
        module._wait_for_ack = lambda *args, **kwargs: True


def test_apply_play_reports_failure_when_no_ack_on_reuse() -> None:
    module._last_seen = module.time.time()  # 既存タブありと判定させる
    module._wait_for_ack = lambda *a, **kw: False
    try:
        message = module._apply_play("file", "\\\\wsl.localhost\\Ubuntu\\tmp\\demo.mp4", 3, "CH3")
        assert "応答が確認できません" in message
        assert message != "CH3の再生に切り替えました。"
    finally:
        module._wait_for_ack = lambda *args, **kwargs: True


def test_apply_render_choices_reports_failure_when_no_ack() -> None:
    module._choice_last_seen = None
    module._wait_for_ack = lambda *a, **kw: False
    try:
        message = module._apply_render_choices(
            [
                {
                    "thumbnail_path": "\\\\wsl.localhost\\Ubuntu\\tmp\\a.png",
                    "label": "候補A",
                    "source_value": "\\\\wsl.localhost\\Ubuntu\\tmp\\a.mp4",
                }
            ]
        )
        assert "応答が確認できません" in message
    finally:
        module._wait_for_ack = lambda *args, **kwargs: True


def test_apply_render_picture_reports_failure_when_no_ack() -> None:
    module._picture_last_seen = None
    module._wait_for_ack = lambda *a, **kw: False
    try:
        message = module._apply_render_picture("\\\\wsl.localhost\\Ubuntu\\tmp\\pic.png")
        assert "応答が確認できません" in message
    finally:
        module._wait_for_ack = lambda *args, **kwargs: True


def test_wait_for_ack_true_when_last_seen_advances_past_call_time() -> None:
    call_time = module.time.time()
    holder = {"seen": call_time + 0.05}
    assert _real_wait_for_ack(lambda: holder["seen"], call_time, timeout=1.0) is True


def test_wait_for_ack_false_when_last_seen_never_advances() -> None:
    call_time = module.time.time()
    assert _real_wait_for_ack(lambda: call_time - 10, call_time, timeout=0.3) is False


def test_open_in_browser_launches_pinned_edge_when_found() -> None:
    tmp_dir = Path(tempfile.mkdtemp())
    fake_edge = tmp_dir / "msedge.exe"
    fake_edge.write_bytes(b"")
    launched = []
    original_candidates = module._EDGE_CANDIDATES
    original_popen = module.subprocess.Popen
    module._EDGE_CANDIDATES = [str(fake_edge)]
    module.subprocess.Popen = lambda args, **kw: launched.append(args)
    try:
        _real_open_in_browser("file:///C:/dummy.html")
        assert launched == [[str(fake_edge), "file:///C:/dummy.html"]]
    finally:
        module._EDGE_CANDIDATES = original_candidates
        module.subprocess.Popen = original_popen
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_open_in_browser_falls_back_to_webbrowser_when_edge_missing() -> None:
    tmp_dir = Path(tempfile.mkdtemp())
    opened = []
    original_candidates = module._EDGE_CANDIDATES
    module._EDGE_CANDIDATES = [str(tmp_dir / "no_such_edge.exe")]
    module.webbrowser.open = lambda uri: opened.append(uri)
    try:
        _real_open_in_browser("file:///C:/dummy.html")
        assert opened == ["file:///C:/dummy.html"]
    finally:
        module._EDGE_CANDIDATES = original_candidates
        module.webbrowser.open = lambda *args, **kwargs: None
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_instance_id_is_set_and_differs_across_process_restarts() -> None:
    assert isinstance(module.INSTANCE_ID, str) and module.INSTANCE_ID

    # 新しいプロセス起動をシミュレートするため、同じserver.pyをもう一度
    # ロードし直す(モジュール内で毎回uuid4()するので、正しく実装されていれば
    # 必ず異なる値になる。制御HTTPサーバーの二重bindはOSErrorとして
    # 静かに失敗しis_leader=Falseになるだけなので、後始末は不要)。
    other_spec = importlib.util.spec_from_file_location(
        "media_renderer_server_other_instance", Path(__file__).with_name("server.py")
    )
    other = importlib.util.module_from_spec(other_spec)
    assert other_spec and other_spec.loader
    other_spec.loader.exec_module(other)
    assert other.INSTANCE_ID != module.INSTANCE_ID


def test_get_status_returns_current_state() -> None:
    module._apply_play(
        "file", "\\\\wsl.localhost\\Ubuntu\\tmp\\demo.mp4", 2, "CH2", tag="ダミーtag"
    )

    status = module._get_status()

    assert status["channel"] == 2
    assert status["title"] == "CH2"
    assert status["tag"] == "ダミーtag"
    assert status["command"] == "play"
