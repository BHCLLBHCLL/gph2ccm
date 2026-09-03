"""Locate sibling toolchains (gphdecoding) and import their APIs."""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from types import ModuleType

_REPO_ROOT = Path(__file__).resolve().parent.parent
_CGNS_PARENT = _REPO_ROOT.parent  # typically D:\training\cgns


def _env_path(name: str) -> Path | None:
    raw = os.environ.get(name)
    if not raw:
        return None
    p = Path(raw).expanduser().resolve()
    return p if p.is_dir() else None


def find_gphdecoding_root() -> Path:
    """Return the directory containing ``gph2cgns.py`` / ``gph_model.py``."""
    env = _env_path("GPH2CCM_GPHDECODING")
    if env is not None and (env / "gph2cgns.py").is_file():
        return env
    candidates = [
        _CGNS_PARENT / "gphdecoding",
        _REPO_ROOT / "vendor" / "gphdecoding",
        _REPO_ROOT / "third_party" / "gphdecoding",
    ]
    for c in candidates:
        if (c / "gph2cgns.py").is_file():
            return c.resolve()
    raise FileNotFoundError(
        "Cannot find gphdecoding (need gph2cgns.py). "
        "Set GPH2CCM_GPHDECODING to the gphdecoding root, or place it at "
        f"{_CGNS_PARENT / 'gphdecoding'}."
    )


def _ensure_on_path(root: Path) -> None:
    s = str(root)
    if s not in sys.path:
        sys.path.insert(0, s)


def import_gph2cgns() -> ModuleType:
    """Import ``gph2cgns`` from the gphdecoding tree."""
    root = find_gphdecoding_root()
    _ensure_on_path(root)
    return importlib.import_module("gph2cgns")


def import_fph2cgns() -> ModuleType:
    """Import ``fph2cgns`` (FPH result-file parser) from the gphdecoding tree.

    Requires ``h5py`` on the import path (fph2cgns hard-checks it); callers
    should surface the ImportError with a pointer to ``pip install h5py``.
    """
    root = find_gphdecoding_root()
    _ensure_on_path(root)
    return importlib.import_module("fph2cgns")

