"""Mesh-topology health checks for a ``.ccm`` file.

Checks relevant to the STAR-CCM+ import ``Reordering`` step:

* per-cell face counts (internal + boundary incidences),
* degenerate (zero-area / collinear) faces,
* repeated vertices inside a face,
* duplicate faces (same ordered vertex sequence),
* cell closure on a sampled subset (every cell edge used exactly twice).

Usage:  python tools/topo_check.py <file.ccm> [--sample N]
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gph2ccm.ccmio import (  # noqa: E402
    CCMIO,
    K_CCMIO_BOUNDARY_FACES,
    K_CCMIO_CELLS,
    K_CCMIO_INTERNAL_FACES,
    K_CCMIO_PROCESSOR,
    K_CCMIO_TOPOLOGY,
)
from tools.dump_ccm import first_entity, parse_stream  # noqa: E402


def face_areas(coords: np.ndarray, vids: np.ndarray, starts: np.ndarray,
               npe: np.ndarray) -> np.ndarray:
    """Newell polygon area magnitude for every face (vectorised)."""
    n = npe.size
    if n == 0:
        return np.empty(0, np.float64)
    p = coords[vids - 1].astype(np.float64)
    face_start = np.repeat(np.cumsum(npe) - npe, npe)
    face_len = np.repeat(npe, npe)
    pos = np.arange(vids.size) - face_start
    nxt = np.where(pos + 1 < face_len, pos + 1, 0)
    q = coords[vids[face_start + nxt] - 1].astype(np.float64)
    cross = np.cross(p, q)
    sums = np.add.reduceat(cross, np.cumsum(npe) - npe)
    return 0.5 * np.linalg.norm(sums, axis=1)


def edge_keys(vids: np.ndarray, starts: np.ndarray, npe: np.ndarray):
    """Undirected edge keys for every face edge (vectorised)."""
    face_start = np.repeat(np.cumsum(npe) - npe, npe)
    face_len = np.repeat(npe, npe)
    pos = np.arange(vids.size) - face_start
    nxt = np.where(pos + 1 < face_len, pos + 1, 0)
    a = vids
    b = vids[face_start + nxt]
    lo = np.minimum(a, b)
    hi = np.maximum(a, b)
    return lo, hi


def analyze(path: str, sample: int = 200_000) -> dict:
    t0 = time.perf_counter()
    ccmio = CCMIO()
    root = ccmio.open_file_readonly(path)
    try:
        state, _ = ccmio.get_state(root)
        proc = ccmio.next_entity(state, K_CCMIO_PROCESSOR, 0)
        verts_node, topo, _, _ = ccmio.read_processor(proc)
        _, _, _, coords = ccmio.read_vertices(verts_node)
        n_verts = coords.shape[0]

        cells_node = first_entity(ccmio, topo, K_CCMIO_CELLS)
        _, cell_types = ccmio.read_cells(cells_node)
        n_cells = int(cell_types.size)

        poly_sets = []       # (starts, npe, vids) per faces entity, file order
        face_owner = []      # 0-based owner per faces entity (-1 if absent)
        face_neigh = []      # 0-based neighbour per faces entity (-1 if absent)

        iface = first_entity(ccmio, topo, K_CCMIO_INTERNAL_FACES)
        if iface is not None:
            n_if, _ = ccmio.entity_size(iface)
            _, istream = ccmio.read_faces(iface, K_CCMIO_INTERNAL_FACES)
            icells = ccmio.read_face_cells(iface, K_CCMIO_INTERNAL_FACES)
            starts, npe, vids = parse_stream(istream, int(n_if))
            poly_sets.append((starts, npe, vids))
            face_owner.append(icells[:, 0] - 1)
            face_neigh.append(icells[:, 1] - 1)

        for node in ccmio.iter_entities(topo, K_CCMIO_BOUNDARY_FACES):
            n_bf, _ = ccmio.entity_size(node)
            _, bstream = ccmio.read_faces(node, K_CCMIO_BOUNDARY_FACES)
            starts, npe, vids = parse_stream(bstream, int(n_bf))
            poly_sets.append((starts, npe, vids))
            try:
                bcells = ccmio.read_face_cells(node, K_CCMIO_BOUNDARY_FACES)
                face_owner.append(bcells[:, 0] - 1)
                face_neigh.append(np.full(int(n_bf), -1, dtype=np.int64))
            except Exception:
                face_owner.append(np.full(int(n_bf), -1, dtype=np.int64))
                face_neigh.append(np.full(int(n_bf), -1, dtype=np.int64))

        owner = np.concatenate(face_owner) if face_owner else np.empty(0, np.int64)
        neigh = np.concatenate(face_neigh) if face_neigh else np.empty(0, np.int64)
        total_faces = int(owner.size)

        # ---- per-cell face counts ---------------------------------------
        inc = np.concatenate([owner, neigh[neigh >= 0]])
        inc = inc[inc >= 0]
        cell_faces = np.bincount(inc, minlength=n_cells)

        # ---- per-face polygon checks (computed per entity, then merged) ---
        n_repeated = 0
        n_dup_face = None
        edge_parts = []   # (efid, elo, ehi) per entity
        n_faces_total = 0
        for set_idx, (starts, npe, vids) in enumerate(poly_sets):
            n_faces = int(npe.size)
            if vids.size:
                face_start = np.repeat(np.cumsum(npe) - npe, npe)
                face_len = np.repeat(npe, npe)
                pos = np.arange(vids.size) - face_start
                nxt = np.where(pos + 1 < face_len, pos + 1, 0)
                n_repeated += int(np.count_nonzero(
                    vids[face_start + nxt] == vids
                ))
                lo, hi = edge_keys(vids, starts, npe)
                edge_parts.append((
                    np.repeat(np.arange(n_faces, dtype=np.int64), npe)
                    + n_faces_total,
                    lo,
                    hi,
                ))
            n_faces_total += n_faces

        max_npe = int(max(p[1].max() for p in poly_sets)) if poly_sets else 0
        if 0 < n_faces_total <= 6_000_000 and max_npe:
            padded = np.zeros((n_faces_total, max_npe), dtype=np.int32)
            rows = []
            cols = []
            vals = []
            row_off = 0
            for starts, npe, vids in poly_sets:
                n_faces = int(npe.size)
                if vids.size:
                    rows.append(np.repeat(np.arange(n_faces), npe) + row_off)
                    cols.append(
                        np.arange(vids.size)
                        - np.repeat(np.cumsum(npe) - npe, npe)
                    )
                    vals.append(vids)
                row_off += n_faces
            if rows:
                padded[np.concatenate(rows), np.concatenate(cols)] = np.concatenate(vals)
                _uniq, counts = np.unique(padded, axis=0, return_counts=True)
                n_dup_face = int((counts > 1).sum())
        elif max_npe and n_faces_total > 6_000_000:
            n_dup_face = "skipped (too many faces)"

        areas = np.concatenate([
            face_areas(coords, p[2], p[0], p[1]) for p in poly_sets
        ]) if poly_sets else np.empty(0, np.float64)

        # ---- cell closure check on a sample ------------------------------
        closure = None
        if n_cells:
            n_sample = min(sample, n_cells)
            sel = np.arange(n_sample)
            fid = np.arange(total_faces)
            cells_a = np.concatenate([owner, neigh])
            faces_a = np.concatenate([fid, fid])
            valid = (cells_a >= 0) & (cells_a < n_sample)
            cf_cell = cells_a[valid]
            cf_fid = faces_a[valid]
            del cells_a, faces_a, valid

            if edge_parts:
                efid = np.concatenate([e[0] for e in edge_parts])
                elo = np.concatenate([e[1] for e in edge_parts])
                ehi = np.concatenate([e[2] for e in edge_parts])
                # keep edges of faces used by the sampled cells
                used_face = np.zeros(total_faces, dtype=bool)
                used_face[cf_fid] = True
                keep = used_face[efid]
                elo = elo[keep]
                ehi = ehi[keep]
                efid = efid[keep]
                m = n_verts + 1
                # every face contributes its edges once to the owner cell and,
                # if internal, once to the neighbour cell
                cell_o = owner[efid]
                cell_n = neigh[efid]
                key_o = cell_o * m * m + elo * m + ehi
                key_n = cell_n * m * m + elo * m + ehi
                keep_o = (cell_o >= 0) & (cell_o < n_sample)
                keep_n = (cell_n >= 0) & (cell_n < n_sample)
                key = np.concatenate([key_o[keep_o], key_n[keep_n]])
                uniq, counts = np.unique(key, return_counts=True)
                closure = {
                    "n_cells_sampled": int(n_sample),
                    "n_cell_edges": int(uniq.size),
                    "n_edges_not_twice": int((counts != 2).sum()),
                }

        return {
            "path": str(path),
            "n_vertices": int(n_verts),
            "n_cells": int(n_cells),
            "n_faces": int(total_faces),
            "per_cell_faces": {
                "min": int(cell_faces.min()) if n_cells else None,
                "max": int(cell_faces.max()) if n_cells else None,
                "mean": float(cell_faces.mean()) if n_cells else None,
                "hist": {
                    str(int(k)): int(v)
                    for k, v in zip(*np.unique(cell_faces, return_counts=True))
                },
                "n_lt4": int((cell_faces < 4).sum()),
                "n_gt20": int((cell_faces > 20).sum()),
            },
            "faces": {
                "npe_min": int(min(p[1].min() for p in poly_sets))
                if poly_sets else None,
                "npe_max": int(max(p[1].max() for p in poly_sets))
                if poly_sets else None,
                "n_repeated_vertex_in_face": int(n_repeated),
                "n_duplicate_faces": n_dup_face,
                "n_zero_area": int((areas < 1e-12).sum()) if areas.size else None,
                "area_min": float(areas.min()) if areas.size else None,
                "area_max": float(areas.max()) if areas.size else None,
            },
            "closure_sample": closure,
            "elapsed_s": round(time.perf_counter() - t0, 1),
        }
    finally:
        ccmio.close_file(root)


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    sample = 200_000
    if "--sample" in sys.argv:
        sample = int(sys.argv[sys.argv.index("--sample") + 1])
    print(json.dumps(analyze(sys.argv[1], sample), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
