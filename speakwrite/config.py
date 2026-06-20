"""Configuration loading, defaults, deep-merge, and validation.

The defaults here are the source of truth for all config keys.
User config from ~/.config/speakwrite/config.yaml is deep-merged over these.
See README.md for the full annotated config reference.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

# Source of truth: these keys and defaults are documented in README.md.
DEFAULTS: dict[str, Any] = {
    "engine": "parakeet",           # parakeet | apple | whisper | mock
    "hotkeys": {
        "dictate": ["ctrl", "alt", "`"],
        "mode": "toggle",
    },
    "hud": {
        "show": True,
        "position": "center",
        "width_pct": 30,
        "lines": 6,
        "font_size": 20,
        "opacity": 0.92,
        "padding": 22,
        "fade_after_sentences": 2,
        "reanchor_pulse_after_s": 3,
        "linger_ms": 800,
    },
    "polish": "punctuation",
    "inject": {
        "method": "paste",
        "trailing_space": True,
    },
    "plan_lane": {
        "enabled": False,
    },
}

# Enum-ish keys validated for clear errors. Maps a dotted path to allowed values.
_ENUMS: dict[str, set[str]] = {
    "engine": {"parakeet", "apple", "whisper", "mock"},
    "hotkeys.mode": {"push_to_talk", "toggle"},
    "polish": {"none", "punctuation", "light", "full"},
    "inject.method": {"paste", "type"},
}

# Valid string values for hud.position.
_HUD_POSITION_STRINGS = {"center", "bottom-center", "top-center", "mouse"}


class ConfigError(Exception):
    """Raised when a config file is malformed or contains invalid values."""


def default_config_path() -> Path:
    """The canonical config location, honoring XDG_CONFIG_HOME."""
    base = os.environ.get("XDG_CONFIG_HOME")
    if base:
        return Path(base) / "speakwrite" / "config.yaml"
    return Path.home() / ".config" / "speakwrite" / "config.yaml"


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge ``override`` onto a copy of ``base``."""
    result = dict(base)
    for key, value in override.items():
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(value, dict)
        ):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _get_dotted(cfg: dict[str, Any], dotted: str) -> Any:
    node: Any = cfg
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _validate(cfg: dict[str, Any]) -> None:
    # Enum validation.
    for dotted, allowed in _ENUMS.items():
        value = _get_dotted(cfg, dotted)
        if value is not None and value not in allowed:
            allowed_str = ", ".join(sorted(allowed))
            raise ConfigError(
                f"Invalid value for '{dotted}': {value!r}. "
                f"Allowed values: {allowed_str}."
            )

    # hud.position: string enum OR mapping with numeric x and y.
    hud_position = _get_dotted(cfg, "hud.position")
    if hud_position is not None:
        if isinstance(hud_position, str):
            if hud_position not in _HUD_POSITION_STRINGS:
                allowed_str = ", ".join(sorted(_HUD_POSITION_STRINGS))
                raise ConfigError(
                    f"Invalid value for 'hud.position': {hud_position!r}. "
                    f"Allowed string values: {allowed_str}; or a mapping with 'x' and 'y'."
                )
        elif isinstance(hud_position, dict):
            for coord in ("x", "y"):
                v = hud_position.get(coord)
                if not isinstance(v, (int, float)) or isinstance(v, bool):
                    raise ConfigError(
                        f"'hud.position.{coord}' must be a number; got {v!r}."
                    )
        else:
            raise ConfigError(
                f"Invalid value for 'hud.position': {hud_position!r}. "
                f"Must be a string or a mapping with 'x' and 'y'."
            )

    # hud.width_pct: int in 1..100.
    width_pct = _get_dotted(cfg, "hud.width_pct")
    if width_pct is not None:
        if not isinstance(width_pct, int) or isinstance(width_pct, bool) or not (1 <= width_pct <= 100):
            raise ConfigError(
                f"Invalid value for 'hud.width_pct': {width_pct!r}. "
                f"Expected an integer in 1..100."
            )

    # hud.lines: positive int.
    lines = _get_dotted(cfg, "hud.lines")
    if lines is not None:
        if not isinstance(lines, int) or isinstance(lines, bool) or lines <= 0:
            raise ConfigError(
                f"Invalid value for 'hud.lines': {lines!r}. "
                f"Expected a positive integer."
            )

    # hud.font_size: positive number.
    font_size = _get_dotted(cfg, "hud.font_size")
    if font_size is not None:
        if not isinstance(font_size, (int, float)) or isinstance(font_size, bool) or font_size <= 0:
            raise ConfigError(
                f"Invalid value for 'hud.font_size': {font_size!r}. "
                f"Expected a positive number."
            )

    # hud.opacity: float in [0, 1].
    opacity = _get_dotted(cfg, "hud.opacity")
    if opacity is not None:
        if not isinstance(opacity, (int, float)) or isinstance(opacity, bool) or not (0.0 <= opacity <= 1.0):
            raise ConfigError(
                f"Invalid value for 'hud.opacity': {opacity!r}. "
                f"Expected a float in [0, 1]."
            )

    # Non-negative numeric knobs.
    for dotted in (
        "hud.fade_after_sentences",
        "hud.reanchor_pulse_after_s",
        "hud.linger_ms",
        "hud.padding",
    ):
        value = _get_dotted(cfg, dotted)
        if value is not None:
            if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
                raise ConfigError(
                    f"Invalid value for '{dotted}': {value!r}. "
                    f"Expected a non-negative number."
                )


def load_config(path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """Load config from ``path`` (or the default location), merged over defaults.

    Missing file -> defaults. Malformed YAML or a non-mapping top level ->
    ConfigError. Invalid enum values -> ConfigError with a clear message.
    """
    explicit = path is not None
    cfg_path = Path(path).expanduser() if explicit else default_config_path()

    user_cfg: dict[str, Any] = {}
    if cfg_path.exists():
        try:
            raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise ConfigError(f"Failed to parse {cfg_path}: {exc}") from exc
        if raw is None:
            raw = {}
        if not isinstance(raw, dict):
            raise ConfigError(
                f"Config file {cfg_path} must contain a YAML mapping at the top level."
            )
        user_cfg = raw
    elif explicit:
        raise ConfigError(f"config file not found: {cfg_path}")

    merged = _deep_merge(DEFAULTS, user_cfg)
    _validate(merged)
    return merged
