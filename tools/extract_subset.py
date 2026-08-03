"""Extract a cell subset of a parsed GPH mesh and write it as a .ccm file.

Diagnostic helper: importing a small subset into STAR-CCM+ shows whether the
reorder cost scales with mesh size or is caused by a structural mesh property.

Usage:  python tools/extract_subset.py <mesh.gph> <n_cells> <output.ccm>
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, r"D:\training\cgns\gphdecoding")

from gph2ccm.convert import convert_model  # noqa: E402


def extract_subset(mesh: dict, n_cells: int) -> dict:
    ld = mesh["link_data"]
    owner = np.asarray(ld["owner"], dtype=np.int64)
    neigh = np.asarray(ld["neighbor"], dtype=np.int64)
    n_total = int(ld["n_cells"])
    n_cells = min(n_cells, n_total)

    sel = np.arange(n_cells, dtype=np.int64)
    own_sel = np.isin(owner, sel)
    nb_sel = np.isin(neigh, sel)
    keep = own_sel | nb_sel
    fid = np.flatnonzero(keep).astype(np.int64)
    n_faces = int(fid.size)

    # New owner: prefer the selected cell; neighbour only when owner is outside
    new_owner_raw = np.where(own_sel[fid], owner[fid], neigh[fid])
    new_neigh = np.full(n_faces, -1, dtype=np.int64)
    both = own_sel[fid] & nb_sel[fid]
    new_neigh[both] = neigh[fid[both]]

    cell_remap = np.full(n_total, -1, dtype=np.int64)
    cell_remap[sel] = np.arange(n_cells, dtype=np.int64)
    new_owner = cell_remap[new_owner_raw]
    new_neigh = np.where(new_neigh >= 0, cell_remap[new_neigh], -1)

    npe = np.asarray(ld["npe"], dtype=np.int64)[fid]
    face_offsets = np.zeros(n_faces + 1, dtype=np.int64)
    np.cumsum(npe, out=face_offsets[1:])
    full_fo = np.asarray(ld["face_offsets"], dtype=np.int64)
    fn = np.asarray(ld["face_nodes"], dtype=np.int64)
    t0 = time.perf_counter()
    face_nodes = np.concatenate(
        [fn[full_fo[f] : full_fo[f + 1]] for f in fid]
    ) if n_faces else np.empty(0, dtype=np.int64)

    used_v = np.unique(face_nodes)
    v_remap = np.full(int(mesh["n_vertices"]), -1, dtype=np.int64)
    v_remap[used_v] = np.arange(used_v.size, dtype=np.int64)
    face_nodes = v_remap[face_nodes]
    vertices = np.asarray(mesh["vertices"], dtype=np.float64)[used_v]

    # surface regions restricted to new boundary faces
    boundary_local = np.flatnonzero(new_neigh == -1)
    old_of_boundary = fid[boundary_local]
    regions = []
    for name, old_ids in mesh.get("surface_regions") or []:
        old_ids = np.asarray(old_ids, dtype=np.int64)
        local = np.searchsorted(old_of_boundary, old_ids)
        hit = local < old_of_boundary.size
        local = local[hit]
        keep_ids = np.isin(old_ids, old_of_boundary)
        local = np.searchsorted(old_of_boundary, old_ids[keep_ids])
        if local.size:
            regions.append((name, boundary_local[local]))

    # cell types
    cvol = np.asarray(mesh.get("cvol_id"), dtype=np.int64)
    parts = mesh.get("parts_with_cvol") or []
    sel_cvol = cvol[sel] if cvol is not None and cvol.size == n_total else None
    if sel_cvol is not None:
        present = set(int(v) for v in np.unique(sel_cvol))
        parts = [(n, s) for n, s in parts if any(v in present for v in (
            s if isinstance(s, (set, frozenset, list, tuple)) else (s,)
        ))]
        if not parts:
            parts = [("cells", int(sel_cvol[0]))]
    else:
        parts = [("cells", 1)]

    sub = {
        "vertices": vertices,
        "n_vertices": int(vertices.shape[0]),
        "link_data": {
            "n_faces": n_faces,
            "n_cells": n_cells,
            "npe": npe,
            "face_nodes": face_nodes,
            "face_offsets": face_offsets,
            "owner": new_owner,
            "neighbor": new_neigh,
            "boundary_faces": boundary_local.tolist(),
        },
        "cvol_id": sel_cvol,
        "parts_with_cvol": parts,
        "volume_regions": ["FluidRegion"],
        "surface_regions": regions,
    }
    print(
        f"subset: {n_cells} cells, {n_faces} faces, "
        f"{boundary_local.size} boundary faces, {regions} "
        f"[{time.perf_counter() - t0:.1f}s]"
    )
    return sub


def main() -> int:
    gph = Path(sys.argv[1])
    n_cells = int(sys.argv[2])
    out = Path(sys.argv[3])
    import gph2cgns

    mesh = gph2cgns.parse_gph_mesh(str(gph))
    sub = extract_subset(mesh, n_cells)
    convert_model(sub, out, title=f"subset-{n_cells}", verbose=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

