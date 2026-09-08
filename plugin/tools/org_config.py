"""Canonical organization configuration, relative to the repository root."""

from __future__ import annotations

from pathlib import Path

ORG_PATH = ".github/agent-org/org.json"


def safe_path(root, relative):
    root = Path(root).resolve()
    relative = Path(relative)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"Unsafe relative path: {relative}")
    result = root / relative
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink() or getattr(current, "is_junction", lambda: False)():
            raise ValueError(f"Redirected configuration/runtime path: {current}")
    if result.resolve() != result:
        raise ValueError(f"Redirected configuration/runtime path: {result}")
    return result


def config_path(root, *, explicit=None, allow_uninstalled=False):
    """Resolve the canonical config or an explicit candidate, without discovery.

    ``allow_uninstalled`` returns the selected path even when it is absent; callers
    distinguish an uninstalled repository from an incomplete installation.
    """
    root = Path(root).resolve()
    if explicit is None:
        selected = safe_path(root, ORG_PATH)
    else:
        candidate = Path(explicit)
        selected = candidate if candidate.is_absolute() else safe_path(root, candidate)
    if selected.is_file() or allow_uninstalled:
        return selected
    label = "canonical organization" if explicit is None else "organization candidate"
    raise FileNotFoundError(f"Missing {label}: {selected}")
