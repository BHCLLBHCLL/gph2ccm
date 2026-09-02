"""Performance baseline for the GPH -> CCM conversion pipeline.

Measures wall-clock time and peak memory of the three conversion stages --
model build (``build_model``), CCM write (``CcmMeshWriter.write``) and
compress (``CCMIOCompress``) -- for a mesh of a given size, plus the output
size.

The default input is a *synthetic structured hexahedral block* (``N^3`` cells,
``(N+1)^3`` vertices) so the baseline is reproducible on any machine, CI
included, without a checked-in multi-hundred-MB mesh file.  Pass ``--gph`` to
benchmark the real converter on a real Cradle mesh instead.

Peak memory is the process *peak working set* (Windows ``PeakWorkingSetSize``
via ``K32GetProcessMemoryInfo``; POSIX ``ru_maxrss``).  It is monotonic, so
reporting it after each stage gives the incremental high-water mark of that
stage and everything before it.

Usage::

    python tools/benchmark.py                 # synthetic 1M-cell smoke run
    python tools/benchmark.py --n 149         # 3,307,949 cells (D3 target)
    python tools/benchmark.py --gph tests/laptop_thermal_...gph
    python tools/benchmark.py --json          # machine-readable summary
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gph2ccm.ccmio import CCMIO  # noqa: E402
from gph2ccm.convert import CcmMeshWriter  # noqa: E402
from gph2ccm.model import build_model  # noqa: E402


# ---------------------------------------------------------------------------
# Peak-memory probe (cross-platform)
# ---------------------------------------------------------------------------

def _peak_rss_windows() -> int:
    class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_ulong),
            ("PageFaultCount", ctypes.c_ulong),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    ctrs = PROCESS_MEMORY_COUNTERS()
    ctrs.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
    k32 = ctypes.windll.kernel32
    k32.GetCurrentProcess.restype = ctypes.c_void_p
    k32.K32GetProcessMemoryInfo.restype = ctypes.c_int
    k32.K32GetProcessMemoryInfo.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(PROCESS_MEMORY_COUNTERS), ctypes.c_ulong
    ]
    k32.K32GetProcessMemoryInfo(
        k32.GetCurrentProcess(), ctypes.byref(ctrs), ctrs.cb
    )
    return int(ctrs.PeakWorkingSetSize)


def _peak_rss_posix() -> int:
    import resource

    return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) * 1024


def peak_rss_bytes() -> int:
    if os.name == "nt":
        return _peak_rss_windows()
    return _peak_rss_posix()


# ---------------------------------------------------------------------------
# Synthetic structured hexahedral mesh
# ---------------------------------------------------------------------------

def synthetic_hex_mesh(n: int) -> dict:
    """Return a ``build_model``-ready mesh dict for an ``n^3`` hex block.

    Cells and vertices use a structured (i, j, k) indexing; each internal
    cell face is stored exactly once with an owner/neighbor pair, and the six
    outer sides become boundary faces with ``neighbor = -1``.
    """
    nv = n + 1
    n_cells = n ** 3
    n_vertices = nv ** 3

    gx, gy, gz = np.meshgrid(
        np.arange(nv), np.arange(nv), np.arange(nv), indexing="ij"
    )
    vertices = np.stack([gx.ravel(), gy.ravel(), gz.ravel()], axis=1).astype(
        np.float64
    ) / float(n)

    def vid(x, y, z):  # vertex id from (x, y, z) grid coords
        return x + y * nv + z * nv * nv

    def cid(i, j, k):  # cell id from (i, j, k) cell coords
        return i + j * n + k * n * n

    J, K = np.meshgrid(np.arange(n), np.arange(n), indexing="ij")
    J = J.ravel().astype(np.int64)
    K = K.ravel().astype(np.int64)

    faces_v: list[np.ndarray] = []
    owners: list[np.ndarray] = []
    neighs: list[np.ndarray] = []
    n_boundary = 0

    def add(fv, own, ngh):
        nonlocal n_boundary
        faces_v.append(fv)
        owners.append(own)
        neighs.append(ngh)
        n_boundary += int(np.count_nonzero(ngh < 0))

    # x-normal faces (plane x = p, p = 0..n)
    for p in range(nv):
        x = np.full(J.shape, p, dtype=np.int64)
        fv = np.stack(
            [vid(x, J, K), vid(x, J + 1, K), vid(x, J + 1, K + 1), vid(x, J, K + 1)],
            axis=1,
        )
        if p == 0:
            own, ngh = cid(0, J, K), np.full(J.shape, -1, dtype=np.int64)
        elif p == n:
            own, ngh = cid(n - 1, J, K), np.full(J.shape, -1, dtype=np.int64)
        else:
            own, ngh = cid(p - 1, J, K), cid(p, J, K)
        add(fv, own, ngh)

    # y-normal faces
    for p in range(nv):
        y = np.full(J.shape, p, dtype=np.int64)
        fv = np.stack(
            [vid(J, y, K), vid(J + 1, y, K), vid(J + 1, y, K + 1), vid(J, y, K + 1)],
            axis=1,
        )
        if p == 0:
            own, ngh = cid(J, 0, K), np.full(J.shape, -1, dtype=np.int64)
        elif p == n:
            own, ngh = cid(J, n - 1, K), np.full(J.shape, -1, dtype=np.int64)
        else:
            own, ngh = cid(J, p - 1, K), cid(J, p, K)
        add(fv, own, ngh)

    # z-normal faces
    for p in range(nv):
        z = np.full(J.shape, p, dtype=np.int64)
        fv = np.stack(
            [vid(J, K, z), vid(J + 1, K, z), vid(J + 1, K + 1, z), vid(J, K + 1, z)],
            axis=1,
        )
        if p == 0:
            own, ngh = cid(J, K, 0), np.full(J.shape, -1, dtype=np.int64)
        elif p == n:
            own, ngh = cid(J, K, n - 1), np.full(J.shape, -1, dtype=np.int64)
        else:
            own, ngh = cid(J, K, p - 1), cid(J, K, p)
        add(fv, own, ngh)

    face_nodes = np.concatenate(faces_v, axis=0).ravel().astype(np.int64)
    owner = np.concatenate(owners).astype(np.int64)
    neighbor = np.concatenate(neighs).astype(np.int64)
    n_faces = int(owner.size)
    npe = np.full(n_faces, 4, dtype=np.int64)
    face_offsets = np.arange(0, 4 * n_faces + 1, 4, dtype=np.int64)

    return {
        "vertices": vertices,
        "n_vertices": n_vertices,
        "link_data": {
            "n_faces": n_faces,
            "n_cells": n_cells,
            "npe": npe,
            "face_nodes": face_nodes,
            "face_offsets": face_offsets,
            "owner": owner,
            "neighbor": neighbor,
            "boundary_faces": np.flatnonzero(neighbor < 0).astype(np.int64),
        },
        "cvol_id": np.ones(n_cells, dtype=np.int64),
        "parts_with_cvol": [("fluid", 1)],
        "volume_regions": ["FluidRegion"],
        "surface_regions": [
            ("xmin", np.flatnonzero(neighbor < 0)[: n * n]),
        ],
    }


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

class Stage:
    def __init__(self, name: str):
        self.name = name
        self.seconds = 0.0
        self.peak_rss = 0


def run_benchmark(
    mesh: dict, chunk_faces: int, compress: bool, ccmio_dll, out: str | None = None
) -> dict:
    ld = mesh["link_data"]

    def measure(stage: Stage, fn):
        t0 = time.perf_counter()
        fn()
        stage.seconds = time.perf_counter() - t0
        stage.peak_rss = peak_rss_bytes()

    stages = {"build": Stage("build_model"), "write": Stage("write"),
              "compress": Stage("compress")}

    model = None
    keep = out is not None
    out_path = Path(out) if keep else (
        Path(os.environ.get("TEMP", "/tmp")) / "gph2ccm_benchmark.ccm"
    )

    def do_build():
        nonlocal model
        model = build_model(mesh)

    measure(stages["build"], do_build)

    ccmio = CCMIO(ccmio_dll)
    if out_path.exists():
        out_path.unlink()
    writer = CcmMeshWriter(
        ccmio, out_path, title="benchmark", chunk_faces=chunk_faces, verbose=False
    )

    def do_write():
        writer.write(model, ld)

    measure(stages["write"], do_write)

    if compress:
        def do_compress():
            ccmio.compress(out_path)

        measure(stages["compress"], do_compress)

    size_mb = out_path.stat().st_size / 1e6 if out_path.exists() else 0.0
    if not keep:
        try:
            out_path.unlink()
        except OSError:
            pass

    result = {
        "n_cells": int(ld["n_cells"]),
        "n_vertices": int(mesh["n_vertices"]),
        "n_faces": int(ld["n_faces"]),
        "output_mb": round(size_mb, 1),
        "chunk_faces": chunk_faces,
        "compress": compress,
    }
    for key, st in stages.items():
        result[key + "_seconds"] = round(st.seconds, 3)
        result[key + "_peak_rss_mb"] = round(st.peak_rss / 1e6, 1)
    return result


def _fmt(result: dict) -> str:
    lines = [
        f"cells      : {result['n_cells']:,}",
        f"vertices   : {result['n_vertices']:,}",
        f"faces      : {result['n_faces']:,}",
        f"output     : {result['output_mb']:.1f} MB",
        "",
        "stage        time(s)   peak RSS(MB)",
        f"build_model  {result['build_seconds']:>8.3f}  {result['build_peak_rss_mb']:>13.1f}",
        f"write        {result['write_seconds']:>8.3f}  {result['write_peak_rss_mb']:>13.1f}",
    ]
    if result["compress"]:
        lines.append(
            f"compress     {result['compress_seconds']:>8.3f}  "
            f"{result['compress_peak_rss_mb']:>13.1f}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python tools/benchmark.py",
        description="GPH->CCM conversion performance baseline",
    )
    ap.add_argument("--n", type=int, default=100,
                    help="synthetic mesh: cells per axis (default 100 -> 1M cells)")
    ap.add_argument("--gph", default=None,
                    help="benchmark a real GPH file instead of the synthetic mesh")
    ap.add_argument("--chunk-faces", type=int, default=500_000)
    ap.add_argument("--no-compress", action="store_true")
    ap.add_argument("--ccmio-dll", default=None)
    ap.add_argument("--out", default=None,
                    help="keep the output .ccm at this path (default: temp + delete)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.gph:
        from gph2ccm.convert import parse_gph

        mesh = parse_gph(args.gph, verbose=False)
    else:
        mesh = synthetic_hex_mesh(args.n)

    result = run_benchmark(
        mesh, args.chunk_faces, not args.no_compress, args.ccmio_dll, args.out
    )

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(_fmt(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
