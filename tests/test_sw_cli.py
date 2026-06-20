"""End-to-end CLI tests for the speakwrite Typer commands (no audio, no MLX).

Driven through main() with no real hardware. The autouse isolated_xdg fixture
(conftest.py) guarantees these never read the developer's real config.
"""

from __future__ import annotations

import json

import pytest

from speakwrite.__main__ import main


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _run(args, *, capsys, monkeypatch=None):
    code = main(args)
    out = capsys.readouterr().out
    return code, out


# ---------------------------------------------------------------------------
# --version
# ---------------------------------------------------------------------------


def test_version(capsys):
    from speakwrite import __version__
    code, out = _run(["--version"], capsys=capsys)
    assert code == 0
    assert __version__ in out


# ---------------------------------------------------------------------------
# config command
# ---------------------------------------------------------------------------


def test_config_outputs_merged_defaults(capsys):
    code, out = _run(["config"], capsys=capsys)
    assert code == 0
    cfg = json.loads(out)
    assert cfg["engine"] == "parakeet"
    assert cfg["hud"]["width_pct"] == 30
    assert cfg["hotkeys"]["dictate"] == ["ctrl", "alt", "`"]


def test_config_has_hud_and_polish(capsys):
    code, out = _run(["config"], capsys=capsys)
    assert code == 0
    cfg = json.loads(out)
    assert "hud" in cfg
    assert "polish" in cfg
    assert "inject" in cfg


def test_config_with_explicit_file(tmp_path, capsys):
    p = tmp_path / "config.yaml"
    p.write_text("hud:\n  width_pct: 75\n")
    code, out = _run(["config", "--config", str(p)], capsys=capsys)
    assert code == 0
    cfg = json.loads(out)
    assert cfg["hud"]["width_pct"] == 75
    # Other hud keys still present.
    assert cfg["hud"]["lines"] == 6


def test_config_missing_explicit_path_exits_2(tmp_path, capsys):
    code, out = _run(["config", "--config", str(tmp_path / "nope.yaml")], capsys=capsys)
    assert code == 2


def test_config_invalid_engine_exits_2(tmp_path, capsys):
    p = tmp_path / "config.yaml"
    p.write_text("engine: bad_engine\n")
    code, out = _run(["config", "--config", str(p)], capsys=capsys)
    assert code == 2


# ---------------------------------------------------------------------------
# stream command — mock engine only
# ---------------------------------------------------------------------------


def test_stream_mock_emits_partials(capsys):
    code, out = _run(["stream", "--engine", "mock"], capsys=capsys)
    assert code == 0
    lines = [l for l in out.splitlines() if l.strip()]
    # Must have at least one partial, one final, one done.
    objects = [json.loads(l) for l in lines]
    events = [o.get("event") for o in objects]
    assert "final" in events
    assert "done" in events
    # At least one partial (no "event" key).
    partials = [o for o in objects if "event" not in o]
    assert len(partials) >= 1


def test_stream_mock_partials_have_text_and_volatile(capsys):
    code, out = _run(["stream", "--engine", "mock"], capsys=capsys)
    lines = [l for l in out.splitlines() if l.strip()]
    objects = [json.loads(l) for l in lines]
    partials = [o for o in objects if "event" not in o]
    for p in partials:
        assert "text" in p
        assert "volatile" in p
        assert isinstance(p["volatile"], bool)


def test_stream_mock_final_before_done(capsys):
    code, out = _run(["stream", "--engine", "mock"], capsys=capsys)
    lines = [l for l in out.splitlines() if l.strip()]
    objects = [json.loads(l) for l in lines]
    events = [o.get("event") for o in objects]
    final_idx = events.index("final")
    done_idx = events.index("done")
    assert final_idx < done_idx


def test_stream_mock_final_text_is_polished(capsys):
    code, out = _run(["stream", "--engine", "mock"], capsys=capsys)
    lines = [l for l in out.splitlines() if l.strip()]
    objects = [json.loads(l) for l in lines]
    final_obj = next(o for o in objects if o.get("event") == "final")
    text = final_obj["text"]
    # Punctuation polish: should start capitalized and end with punctuation.
    assert text[0].isupper()
    assert text[-1] in ".?!"


def test_stream_mock_config_override(tmp_path, capsys):
    """--config override is read, --engine still works."""
    p = tmp_path / "sw.yaml"
    p.write_text("polish: none\n")
    code, out = _run(["stream", "--config", str(p), "--engine", "mock"], capsys=capsys)
    assert code == 0
    lines = [l for l in out.splitlines() if l.strip()]
    objects = [json.loads(l) for l in lines]
    assert any(o.get("event") == "done" for o in objects)


def test_stream_unbuilt_engine_exits_3(capsys):
    """Engines that aren't built yet (apple, whisper) exit with code 3."""
    code, out = _run(["stream", "--engine", "apple"], capsys=capsys)
    assert code == 3


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


def test_no_command_is_error(capsys):
    code, out = _run([], capsys=capsys)
    assert code != 0


def test_unknown_command_is_error(capsys):
    code, out = _run(["bogus"], capsys=capsys)
    assert code != 0
