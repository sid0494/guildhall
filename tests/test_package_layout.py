"""Guards the package layout chosen in docs/adr/0003-single-package-src-layout.md."""

import sys
from pathlib import Path

import pytest

import guildhall

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize(
    "directory", [REPO_ROOT, REPO_ROOT / "src"], ids=["repo-root", "src"]
)
def test_no_top_level_module_shadows_the_standard_library(directory: Path) -> None:
    clashes = sorted(
        entry.name
        for entry in directory.iterdir()
        if (entry.is_dir() or entry.suffix == ".py")
        and entry.stem in sys.stdlib_module_names
    )
    assert clashes == []


def test_guildhall_is_imported_from_the_src_layout() -> None:
    assert Path(guildhall.__file__).parent == REPO_ROOT / "src" / "guildhall"
