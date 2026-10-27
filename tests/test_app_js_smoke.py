"""Load app.js in node and fire its DOMContentLoaded init list.

The UI has no build step and no JS tests, so a stale reference (a function
deleted while its call sites stayed) reaches the browser unnoticed and takes
out every page: one ReferenceError in the init list stops every render call
after it. That happened once — initDesignViewModal was deleted with the
task-drafts UI while its call site survived — and pytest stayed green through
it. This test is the cheap floor: does the file actually run.
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_app_js_loads_and_initializes():
    result = subprocess.run(
        [
            "node",
            str(REPO_ROOT / "tests" / "js_smoke.js"),
            str(REPO_ROOT / "app" / "static" / "app.js"),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"app.js failed to load/init:\n{result.stderr}"
    )
    assert "OK" in result.stdout


def _jsdom_available():
    """jsdom is optional: it is not a project dependency, so the diagram check
    runs where someone installed it and skips (loudly, in the reason) elsewhere.
    Point HARNOQ_JSDOM at an installed copy's lib/api.js to enable it."""
    if os.environ.get("HARNOQ_JSDOM"):
        return True
    # Decorator ARGUMENTS are evaluated at import time, so this runs before the
    # skipif guarding on node's presence gets a chance to skip — without this
    # check a machine with no node at all fails COLLECTION (taking the whole
    # suite with it) instead of skipping one test.
    if shutil.which("node") is None:
        return False
    probe = subprocess.run(
        ["node", "--input-type=module", "-e", "await import('jsdom')"],
        capture_output=True,
        text=True,
    )
    return probe.returncode == 0


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
@pytest.mark.skipif(
    not _jsdom_available(),
    reason="jsdom not installed (set HARNOQ_JSDOM=<path>/jsdom/lib/api.js)",
)
def test_diagram_definitions_parse_as_mermaid():
    """Every mermaid definition app.js builds must parse.

    A malformed definition is invisible to pytest and to `node --check`; it
    fails only when a browser draws it, and then the whole diagram disappears.
    Parsing is the part checkable without a browser — the pixels still need a
    human look.
    """
    result = subprocess.run(
        ["node", str(REPO_ROOT / "tests" / "js_diagram_smoke.mjs")],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, (
        f"diagram definitions failed to parse:\n{result.stdout}\n{result.stderr}"
    )
    assert "captured 3 definitions" in result.stdout
