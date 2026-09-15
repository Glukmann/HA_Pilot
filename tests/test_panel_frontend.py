"""Tests for the panel frontend bundle build."""

from pathlib import Path
import subprocess

import pytest

FRONTEND_DIR = Path(__file__).parent.parent / "custom_components" / "pilot" / "frontend"
ESBUILD = FRONTEND_DIR / "node_modules" / ".bin" / "esbuild"
PANEL_JS = FRONTEND_DIR / "panel.js"

needs_esbuild = pytest.mark.skipif(
    not ESBUILD.exists(), reason="frontend devDependencies not installed"
)

# esbuild only emits these legacy-transform markers when the TS decorators
# compiled as legacy (experimentalDecorators). In standard-decorator mode the
# field decorators (@property/@state) hit Lit's "Unsupported decorator
# location: field" throw at class definition time — a black panel.
LEGACY_MARKERS = [
    "__decorateClass",
    'PilotPanel.prototype, "_status"',
    '("pilot-panel")',
]


def _assert_legacy_bundle(bundle: str) -> None:
    for marker in LEGACY_MARKERS:
        assert marker in bundle, f"missing legacy-decorator marker: {marker}"


def test_committed_bundle_uses_legacy_decorators():
    """Regression: the checked-in panel.js must be a legacy-decorator build."""
    _assert_legacy_bundle(PANEL_JS.read_text(encoding="utf-8"))


@needs_esbuild
def test_fresh_build_uses_legacy_decorators(tmp_path):
    """The build script must produce a legacy-decorator bundle from scratch."""
    outfile = tmp_path / "panel.js"
    subprocess.run(
        [
            str(ESBUILD),
            "src/panel.ts",
            "--bundle",
            "--format=iife",
            "--target=es2022",
            "--tsconfig=tsconfig.json",
            f"--outfile={outfile}",
        ],
        cwd=FRONTEND_DIR,
        check=True,
        capture_output=True,
    )
    _assert_legacy_bundle(outfile.read_text(encoding="utf-8"))
