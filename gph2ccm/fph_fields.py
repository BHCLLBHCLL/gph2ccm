"""FPH result-file pipeline (C2 slice 2): solver fields -> SolutionField.

FPH (magic ``CRDL-FLD``) is a superset of GPH: same mesh sections plus an
``LS_SPHFile`` section carrying cell-centred solver results.  The parsing
itself lives in the sibling ``gphdecoding`` tree (``fph2cgns.py``, which
needs ``h5py``); this module turns its ``flow_solution`` dict into validated
:class:`~gph2ccm.model.SolutionField` entries for the CCM writer.

Variable naming conventions handled here (observed on real Cradle FPH files):

* scalars come as-is, e.g. ``PRES``, ``TURK``, ``TEPS``, ``EVIS``, ``TPRS``;
* vectors are split into components with a trailing ``X``/``Y``/``Z``, e.g.
  ``VELX``/``VELY``/``VELZ`` -- reassembled here into one ``(n, 3)`` field
  named after the common base (``VEL``);
* some files carry sentinel values (> 1e20) for inactive cells -- clipped to
  0 by default, mirroring fph2cgns' ``--clip-flow 1`` behaviour.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np

from .deps import import_fph2cgns
from .model import SolutionField

_SENTINEL = 1e20
_AXIS_SUFFIXES = ("X", "Y", "Z")


def _clip_sentinels(arr: np.ndarray) -> np.ndarray:
    """Clear solver sentinels (|v| > 1e20) to 0, matching fph2cgns --clip-flow."""
    out = np.array(arr, dtype=np.float64, copy=True)
    out[np.abs(out) > _SENTINEL] = 0.0
    return out


def group_flow_solution(
    flow_solution: dict[str, np.ndarray],
) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray]]:
    """Split an fph2cgns ``flow_solution`` dict into ``(scalars, vectors)``.

    Vector components (``<base>X`` / ``<base>Y`` / ``<base>Z``) are stacked
    into ``(n, 3)`` arrays keyed by ``<base>``.  A trailing X/Y/Z name whose
    siblings are missing stays a scalar under its full name.
    """
    scalars: dict[str, np.ndarray] = {}
    vectors: dict[str, np.ndarray] = {}
    consumed: set[str] = set()
    for name in flow_solution:
        if name in consumed:
            continue
        if name.endswith(_AXIS_SUFFIXES):
            base = name[:-1]
            trio = [f"{base}{s}" for s in _AXIS_SUFFIXES]
            if all(t in flow_solution for t in trio):
                vec = np.column_stack(
                    [np.asarray(flow_solution[t], dtype=np.float64) for t in trio]
                )
                vectors[base] = vec
                consumed.update(trio)
                continue
        scalars[name] = np.asarray(flow_solution[name], dtype=np.float64)
    return scalars, vectors


def solution_fields_from_flow_solution(
    flow_solution: dict[str, np.ndarray],
    n_cells: Optional[int] = None,
    clip_sentinels: bool = True,
    fields: Optional[list[str]] = None,
) -> list[SolutionField]:
    """Convert an fph2cgns ``flow_solution`` dict to SolutionField entries.

    ``fields`` is an optional whitelist (F2 ``--fields PRES,VEL``): matched
    case-insensitively against the grouped field names -- scalars by their
    own name, vectors by their base name (``VEL`` covers the VELX/Y/Z trio).
    Unmatched whitelist entries are ignored silently (the FPH simply may not
    carry them).

    Raises :class:`ValueError` when *n_cells* is given and any variable does
    not match the mesh cell count (e.g. FPH/ GPH from different runs).
    """
    scalars, vectors = group_flow_solution(flow_solution)
    if fields:
        wanted = {str(f).strip().upper() for f in fields if str(f).strip()}
        scalars = {k: v for k, v in scalars.items() if k.upper() in wanted}
        vectors = {k: v for k, v in vectors.items() if k.upper() in wanted}
    fields_out: list[SolutionField] = []
    for name, arr in scalars.items():
        if clip_sentinels:
            arr = _clip_sentinels(arr)
        fields_out.append(SolutionField(name=name, data=arr))
    for name, arr in vectors.items():
        if clip_sentinels:
            arr = _clip_sentinels(arr)
        fields_out.append(SolutionField(name=name, data=arr))
    if n_cells is not None:
        for f in fields_out:
            if f.data.shape[0] != n_cells:
                raise ValueError(
                    f"FPH field {f.name!r} has {f.data.shape[0]} values but "
                    f"the mesh has {n_cells} cells -- the .fph and the mesh "
                    "appear to come from different runs"
                )
    return fields_out


def load_fph_flow_solution(
    fph_path: str | Path, verbose: bool = True
) -> dict[str, np.ndarray]:
    """Parse *fph_path* and return the raw ``flow_solution`` dict.

    Keys are Cradle variable names (scalars as-is, vectors split into
    ``<base>X/Y/Z`` components), values are per-cell ``float64`` arrays.
    Raises :class:`ValueError` when the file has no ``LS_SPHFile`` section.
    """
    try:
        fph2cgns = import_fph2cgns()
    except ImportError as exc:
        raise ImportError(
            "fph2cgns requires h5py; install it with: pip install h5py"
        ) from exc
    if verbose:
        print(f"[gph2ccm] reading FPH result data: {fph_path}")
    mesh = fph2cgns.parse_gph_mesh(str(fph_path))
    fs = mesh.get("flow_solution") or {}
    if not fs:
        raise ValueError(
            f"{fph_path}: no LS_SPHFile solution section found "
            "(plain GPH mesh file?)"
        )
    return fs


def load_fph_solution_fields(
    fph_path: str | Path,
    n_cells: Optional[int] = None,
    clip_sentinels: bool = True,
    verbose: bool = True,
) -> list[SolutionField]:
    """Parse *fph_path* (``CRDL-FLD``) and return its solution fields.

    Parses the whole FPH (mesh + results) via ``fph2cgns``; only the
    ``flow_solution`` part is used here, but the mesh keys come back too so
    callers can reuse the same parse for single-file FPH conversion.
    """
    fs = load_fph_flow_solution(fph_path, verbose=verbose)
    fields = solution_fields_from_flow_solution(
        fs, n_cells=n_cells, clip_sentinels=clip_sentinels
    )
    if verbose:
        print(f"[gph2ccm] FPH: {len(fs)} variable(s) -> {len(fields)} solution field(s)")
    return fields
