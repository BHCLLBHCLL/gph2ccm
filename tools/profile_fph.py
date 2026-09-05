"""F1 -- FPH parse profiler: split ``parse_gph_mesh`` wall-clock into
mesh decode vs flow-solution (LS_SPHFile) field read.

Method: ``fph2cgns.parse_gph_mesh`` returns mesh *and* ``flow_solution`` in
one call, so the split is done by wrapping ``fph2cgns._parse_fph_flow_solution``
with a timer (monkeypatch) before invoking the real parse.  The remainder is
mesh decode + zone partitioning.

Usage::

    python tools/profile_fph.py <file.fph> [--json]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gph2ccm.deps import find_gphdecoding_root  # noqa: E402


def profile(fph_path: Path) -> dict:
    root = Path(find_gphdecoding_root())
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    import fph2cgns

    timings: dict[str, float] = {}
    orig = fph2cgns._parse_fph_flow_solution

    def timed(data, n_cells):
        t0 = time.perf_counter()
        try:
            return orig(data, n_cells)
        finally:
            timings["fields"] = time.perf_counter() - t0

    fph2cgns._parse_fph_flow_solution = timed
    try:
        t0 = time.perf_counter()
        mesh = fph2cgns.parse_gph_mesh(str(fph_path))
        total = time.perf_counter() - t0
    finally:
        fph2cgns._parse_fph_flow_solution = orig

    n_cells = int((mesh.get("link_data") or {}).get("n_cells") or 0)
    n_vars = len(mesh.get("flow_solution") or {})
    fields = timings.get("fields", 0.0)
    return {
        "file": str(fph_path),
        "size_mb": round(fph_path.stat().st_size / 1e6, 1),
        "n_cells": n_cells,
        "n_variables": n_vars,
        "total_seconds": round(total, 2),
        "fields_seconds": round(fields, 2),
        "mesh_seconds": round(total - fields, 2),
        "fields_pct": round(100 * fields / total, 1) if total else 0.0,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("fph", help=".fph file (or .gph; field stage reports 0)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    result = profile(Path(args.fph))
    if args.json:
        print(json.dumps(result, indent=2))
        return 0
    print(f"file            : {result['file']}")
    print(f"size            : {result['size_mb']} MB")
    print(f"cells           : {result['n_cells']:,}")
    print(f"variables       : {result['n_variables']}")
    print(f"total parse     : {result['total_seconds']} s")
    print(f"  field read    : {result['fields_seconds']} s "
          f"({result['fields_pct']}%)")
    print(f"  mesh decode   : {result['mesh_seconds']} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
