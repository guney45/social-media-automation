"""Locate a Chromium build for Playwright.

Playwright normally manages its own browser download, but managed images (CI
runners, this project's Docker image, Claude Code's sandbox) often ship a build
whose revision does not match the installed Playwright version. Rather than
re-downloading a browser on every run, we look for one that already exists.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

from smauto.logging import get_logger

log = get_logger(__name__)

#: Explicit override, checked first.
ENV_VAR = "SMAUTO_CHROMIUM_PATH"

_SEARCH_ROOTS = (
    os.environ.get("PLAYWRIGHT_BROWSERS_PATH"),
    "/opt/pw-browsers",
    str(Path.home() / ".cache" / "ms-playwright"),
)

#: Full Chromium first: the headless shell cannot screenshot with a transparent
#: background on every build, and we depend on that.
_BINARY_NAMES = ("chrome", "headless_shell", "chrome-headless-shell")


def find_chromium() -> Path | None:
    """Return a usable Chromium executable, or None to let Playwright decide."""
    override = os.environ.get(ENV_VAR)
    if override:
        candidate = Path(override)
        if candidate.exists():
            return candidate
        log.warning("%s points at a missing file", ENV_VAR, path=override)

    for candidate in _candidates():
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None


def _candidates() -> Iterator[Path]:
    for root in _SEARCH_ROOTS:
        if not root:
            continue
        base = Path(root)
        if not base.is_dir():
            continue
        for name in _BINARY_NAMES:
            # Newest revision first: directory names end with the build number.
            for directory in sorted(base.glob("chromium*"), reverse=True):
                yield from sorted(directory.glob(f"chrome-*/{name}"))


def launch_kwargs() -> dict[str, object]:
    """Keyword arguments for `playwright.chromium.launch()`."""
    kwargs: dict[str, object] = {
        "args": ["--font-render-hinting=none", "--force-color-profile=srgb"],
    }
    executable = find_chromium()
    if executable is not None:
        kwargs["executable_path"] = str(executable)
        log.debug("using chromium", path=str(executable))
    return kwargs


__all__ = ["ENV_VAR", "find_chromium", "launch_kwargs"]
