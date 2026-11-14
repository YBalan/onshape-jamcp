"""Shared helpers for the release-tooling tests (not itself a test module)."""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

CHANGELOG = """# Changelog

Intro text.

## [0.2.0] - 2026-02-01

### Added
- New thing.

## [0.1.0] - 2026-01-01

### Added
- First thing.
- Second thing.
"""


def load_release():
    """Import scripts/release.py (it is a script, not a package module)."""
    spec = importlib.util.spec_from_file_location("release", ROOT / "scripts" / "release.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_repo(root: Path) -> Path:
    """A minimal fake repository with all three version carriers in agreement (0.2.0)."""
    (root / "src" / "onshape_jamcp").mkdir(parents=True)
    (root / "src" / "onshape_jamcp" / "__init__.py").write_text('__version__ = "0.2.0"\n')
    (root / "pyproject.toml").write_text(
        '[project]\nname = "x"\nversion = "0.2.0"\n\n[tool.ruff]\ntarget-version = "py310"\n'
    )
    (root / "CHANGELOG.md").write_text(CHANGELOG)
    (root / "README.md").write_text("readme\n")
    return root
