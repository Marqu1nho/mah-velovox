"""Configuration loading, defaults, deep-merge, and validation.

The defaults here mirror config.example.yaml (§04 of the spec) exactly.
User config from ~/.config/readaloud/config.yaml is deep-merged over these.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

# §04 contract: ship exactly these keys with these defaults.
DEFAULTS: dict[str, Any] = {
    "engine": "say",
    "hotkeys": {
        "toggle": ["ctrl", "alt", "cmd", "S"],
        "read_window": ["ctrl", "alt", "cmd", "W"],
        "show_alerts": True,
    },
    "voice": {
        "say_voice": "system",
        "base_wpm": 240,
        "kokoro_voice": "af_heart",
        "speed": 1.1,
    },
    "headers": {
        "rate_factor": 0.85,
        "pause_before_ms": 500,
        "pause_after_ms": 400,
        "treat_all_caps_lines_as_headers": True,
    },
    "pauses": {
        "paragraph_ms": 350,
        "list_item_ms": 200,
        "horizontal_rule_ms": 600,
    },
    "code_blocks": {
        "mode": "skip",
        "announce_template": "code block, {lines} lines",
    },
    "clean": {
        "rejoin": "smart",
        "urls": "domain",
        "paths": "basename",
        "emoji": "skip",
    },
    "window_read": {
        "max_chars": 20000,
    },
    "limits": {
        "max_selection_chars": 60000,
    },
}

# Enum-ish keys validated for clear errors. Maps a dotted path to allowed values.
_ENUMS: dict[str, set[str]] = {
    "engine": {"say", "kokoro"},
    "code_blocks.mode": {"skip", "read", "silent-skip"},
    "clean.rejoin": {"smart", "always", "never"},
    "clean.urls": {"domain", "full", "skip"},
    "clean.paths": {"basename", "full", "skip"},
    "clean.emoji": {"skip", "name"},
}


class ConfigError(Exception):
    """Raised when a config file is malformed or contains invalid values."""


def default_config_path() -> Path:
    """The canonical config location, honoring XDG_CONFIG_HOME."""
    base = os.environ.get("XDG_CONFIG_HOME")
    if base:
        return Path(base) / "readaloud" / "config.yaml"
    return Path.home() / ".config" / "readaloud" / "config.yaml"


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
    for dotted, allowed in _ENUMS.items():
        value = _get_dotted(cfg, dotted)
        if value is not None and value not in allowed:
            allowed_str = ", ".join(sorted(allowed))
            raise ConfigError(
                f"Invalid value for '{dotted}': {value!r}. "
                f"Allowed values: {allowed_str}."
            )


def load_config(path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """Load config from ``path`` (or the default location), merged over defaults.

    Missing file -> defaults. Malformed YAML or a non-mapping top level ->
    ConfigError. Invalid enum values -> ConfigError with a clear message.
    """
    cfg_path = Path(path) if path is not None else default_config_path()

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

    merged = _deep_merge(DEFAULTS, user_cfg)
    _validate(merged)
    return merged
