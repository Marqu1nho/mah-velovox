"""End-to-end CLI tests for --print-script / --print-config-json (no audio).

Driven through main() with stdin patched. The autouse isolated_xdg fixture
(conftest.py) guarantees these never read the developer's real config; tests
that need specific config pass --config to a tmp file.
"""

import io
import json

import pytest

from readaloud.__main__ import main


def _run(args, stdin_text="", *, capsys, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin_text))
    code = main(args)
    out = capsys.readouterr().out
    return code, out


def test_print_config_json_outputs_merged_defaults(capsys, monkeypatch):
    code, out = _run(["--print-config-json"], "", capsys=capsys, monkeypatch=monkeypatch)
    assert code == 0
    cfg = json.loads(out)
    assert cfg["engine"] in ("say", "kokoro")
    assert cfg["hotkeys"]["toggle"] == ["ctrl", "alt", "cmd", "S"]
    assert cfg["pauses"]["paragraph_ms"] == 350
    # base_wpm calibration and the canvas-pill alert keys survive.
    assert cfg["voice"]["base_wpm"] == 240
    assert cfg["alerts"]["y_pct"] == 3.5
    assert cfg["alerts"]["duration_s"] == 1.2


def test_print_config_json_with_explicit_config(tmp_path, capsys, monkeypatch):
    p = tmp_path / "config.yaml"
    p.write_text("voice:\n  base_wpm: 300\n")
    code, out = _run(
        ["--print-config-json", "--config", str(p)],
        "",
        capsys=capsys,
        monkeypatch=monkeypatch,
    )
    assert code == 0
    cfg = json.loads(out)
    assert cfg["voice"]["base_wpm"] == 300


def test_print_script_header_and_body(capsys, monkeypatch):
    text = "## Title\nSome text follows here.\n"
    code, out = _run(["--print-script"], text, capsys=capsys, monkeypatch=monkeypatch)
    assert code == 0
    chunks = json.loads(out)
    assert chunks[0]["kind"] == "header"
    assert chunks[0]["text"] == "Title"
    assert chunks[0]["rate_factor"] == 0.85
    assert chunks[0]["pause_before_ms"] == 500
    assert chunks[0]["pause_after_ms"] == 400
    assert chunks[1]["kind"] == "paragraph"
    assert chunks[1]["rate_factor"] == 1.0


def test_print_script_full_tui_paste(capsys, monkeypatch):
    text = (
        "╭────────────╮\n"
        "│ \x1b[1mResults\x1b[0m │\n"
        "╰────────────╯\n"
        "## Summary\n"
        "All tests passed and the build finished without\n"
        "errors in the latest run.\n"
        "```\npytest -q\n```\n"
        "- item one\n"
    )
    code, out = _run(["--print-script"], text, capsys=capsys, monkeypatch=monkeypatch)
    assert code == 0
    chunks = json.loads(out)
    kinds = [c["kind"] for c in chunks]
    assert "header" in kinds
    assert "code_announce" in kinds
    assert "list_item" in kinds
    joined = " ".join(c["text"] for c in chunks)
    assert "│" not in joined and "╭" not in joined
    assert "finished without errors" in joined  # hard wrap repaired


def test_print_script_blockquote_is_audible(capsys, monkeypatch):
    # After the prompt-marker fix, a blockquote flows clean -> parse -> script
    # and ends up as a spoken chunk rather than being scrubbed away.
    text = "> quoted wisdom\n> that wraps on\n\nA following paragraph.\n"
    code, out = _run(["--print-script"], text, capsys=capsys, monkeypatch=monkeypatch)
    assert code == 0
    chunks = json.loads(out)
    quote = next(c for c in chunks if c["kind"] == "blockquote")
    assert "quoted wisdom that wraps on" in quote["text"]


def test_print_script_snake_case_survives(capsys, monkeypatch):
    text = "A paragraph mentioning my_var_name and **bold** text.\n"
    code, out = _run(["--print-script"], text, capsys=capsys, monkeypatch=monkeypatch)
    assert code == 0
    joined = " ".join(c["text"] for c in json.loads(out))
    assert "my_var_name" in joined
    assert "**" not in joined


def test_print_script_empty_stdin_yields_empty_list(capsys, monkeypatch):
    code, out = _run(["--print-script"], "   \n", capsys=capsys, monkeypatch=monkeypatch)
    assert code == 0
    assert json.loads(out) == []


def test_requires_a_mode_flag(capsys, monkeypatch):
    # No action flag is a usage error (argparse exits with SystemExit(2)).
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2
