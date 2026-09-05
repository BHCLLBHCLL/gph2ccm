"""E5 -- field-write throughput profiler.

Isolates the cost of writing solution-field data (``_write_solution_fields`` /
``CCMIOWriteFieldDataf``) from the mesh write, to answer the E5 question:
*is field writing slow enough to warrant optimization?*

Method: the same synthetic hexahedral mesh is written three times -- with 0,
4 and 16 solution fields -- and the write stage time is differenced to get
the marginal per-field cost.  A raw ``numpy -> file`` memcpy reference shows
what the disk alone can do, i.e. the floor any implementation must pay.

Usage::

    python tools/profile_fields.py            # 1M cells, 0/4/16 fields
    python tools/profile_fields.py --n 149    # 3.3M cells
    python tools/profile_fields.py --json     # machine-readable summary
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.benchmark import peak_rss_bytes, synthetic_hex_mesh  # noqa: E402

from gph2ccm.ccmio import CCMIO  # noqa: E402
from gph2ccm.convert import CcmMeshWriter  # noqa: E402
from gph2ccm.model import SolutionField, build_model  # noqa: E402


def _make_fields(n_cells: int, count: int) -> list[SolutionField]:
    """*count* fields: 3/4 scalar, 1/4 vector (mirrors the real FPH mix)."""
    rng = np.random.default_rng(42)
    out: list[SolutionField] = []
    n_scalar = (count * 3) // 4
    for i in range(count):
        if i < n_scalar:
            out.append(
                SolutionField(
                    name=f"Field{i}", data=rng.random(n_cells) * 100.0
                )
            )
        else:
            out.append(
                SolutionField(
                    name=f"Vec{i}",
                    data=rng.random((n_cells, 3)),
                )
            )
    return out


def _raw_disk_reference(n_cells: int, n_fields: int, tmp_dir: Path) -> float:
    """Seconds to memcpy ``n_fields`` float32 arrays of n_cells to disk."""
    data = np.zeros(n_cells, dtype=np.float32)
    path = tmp_dir / "raw_ref.bin"
    t0 = time.perf_counter()
    with open(path, "wb") as fh:
        for _ in range(n_fields):
            fh.write(data.tobytes())
    elapsed = time.perf_counter() - t0
    path.unlink(missing_ok=True)
    return elapsed


def profile(n_side: int, field_counts: list[int], chunk_faces: int) -> dict:
    mesh = synthetic_hex_mesh(n_side)
    ld = mesh["link_data"]
    n_cells = int(ld["n_cells"])
    field_mb_per_field = n_cells * 4 / 1e6  # float32 per-cell

    ccmio = CCMIO()
    results: dict = {
        "n_cells": n_cells,
        "field_mb_per_field": round(field_mb_per_field, 2),
        "runs": {},
    }
    times: dict[int, float] = {}
    for count in field_counts:
        model = build_model(
            mesh, solution_fields=_make_fields(n_cells, count)
        )
        out = Path(os.environ.get("TEMP", "/tmp")) / f"gph2ccm_pf_{count}.ccm"
        if out.exists():
            out.unlink()
        writer = CcmMeshWriter(
            ccmio, out, title="profile-fields", chunk_faces=chunk_faces,
            verbose=False,
        )
        rss0 = peak_rss_bytes()
        t0 = time.perf_counter()
        writer.write(model, ld)
        times[count] = time.perf_counter() - t0
        peak = peak_rss_bytes()
        out_mb = out.stat().st_size / 1e6
        out.unlink(missing_ok=True)
        results["runs"][count] = {
            "write_seconds": round(times[count], 3),
            "peak_rss_mb": round(peak / 1e6, 1),
            "delta_rss_mb": round((peak - rss0) / 1e6, 1),
            "output_mb": round(out_mb, 1),
        }

    base = times[field_counts[0]]
    top, top_n = field_counts[-1], field_counts[-1] - field_counts[0]
    marginal = (times[top] - base) / top_n
    total_mb = top_n * field_mb_per_field
    results["marginal_per_field_seconds"] = round(marginal, 4)
    results["field_throughput_mb_s"] = round(total_mb / max(marginal, 1e-9), 1)

    raw_dir = Path(os.environ.get("TEMP", "/tmp"))
    raw = _raw_disk_reference(n_cells, top_n, raw_dir)
    results["raw_disk_seconds"] = round(raw, 3)
    results["raw_disk_mb_s"] = round(total_mb / max(raw, 1e-9), 1)
    results["overhead_ratio"] = round(
        marginal / max(raw / top_n, 1e-9), 1
    )
    return results


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n", type=int, default=100, help="hex block side (n^3 cells)")
    ap.add_argument("--chunk-faces", type=int, default=500000)
    ap.add_argument("--counts", type=int, nargs="+", default=[0, 4, 16])
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    result = profile(args.n, args.counts, args.chunk_faces)
    if args.json:
        print(json.dumps(result, indent=2))
        return 0

    print(f"cells: {result['n_cells']:,}  "
          f"(one scalar field = {result['field_mb_per_field']} MB float32)")
    print()
    print("fields   write(s)   output(MB)   peak RSS(MB)")
    for count, run in result["runs"].items():
        print(f"{count:>6}   {run['write_seconds']:>8.3f}   "
              f"{run['output_mb']:>9.1f}   {run['peak_rss_mb']:>12.1f}")
    print()
    print(f"marginal cost per scalar field : "
          f"{result['marginal_per_field_seconds'] * 1000:.1f} ms")
    print(f"field-write throughput         : "
          f"{result['field_throughput_mb_s']:.1f} MB/s")
    print(f"raw disk memcpy reference      : "
          f"{result['raw_disk_mb_s']:.1f} MB/s "
          f"({result['raw_disk_seconds']:.3f} s total)")
    print(f"CCMIO/ADF overhead vs raw disk : x{result['overhead_ratio']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
