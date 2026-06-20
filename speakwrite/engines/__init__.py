"""Engine factory for speakwrite.

Imports are lazy: only the selected engine's module is imported, so the
top-level import of speakwrite.engines never pulls in parakeet_mlx,
sounddevice, or any other heavy dependency.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .base import Engine


def make_engine(cfg: dict[str, Any], injected: "Engine | None" = None) -> "Engine":
    """Return an Engine instance for the given config.

    If ``injected`` is provided it is returned as-is (for testing / DI).
    Otherwise dispatches on ``cfg["engine"]``:
      - "mock"     → MockEngine (instant, no hardware)
      - "parakeet" / "apple" / "whisper" → raises RuntimeError (not yet built)
    """
    if injected is not None:
        return injected

    name = cfg.get("engine", "parakeet")

    if name == "mock":
        from .mock import MockEngine
        return MockEngine()

    # Lazy import guard — these engines are not yet built.
    # The import is inside the branch so no heavy deps are pulled at module load.
    if name in ("parakeet", "apple", "whisper"):
        raise RuntimeError(f"engine '{name}' not yet built")

    raise RuntimeError(f"unknown engine: '{name}'")
