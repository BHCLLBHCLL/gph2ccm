"""Build a synthetic two-block hex mesh and write a split-region ``.ccm``.

Two fluid blocks (``air_domain`` and ``rotation1``) share a conformal
interface plane.  With ``--split-fluid-regions`` the converter must produce
two independent STAR-CCM+ regions connected by a grid interface.

Usage:  python tools/make_two_region_ccm.py [output.ccm]
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gph2ccm.convert import convert_model  # noqa: E402


def make_two_blocks(nx: int = 10, ny: int = 10, nz: int = 10) -> dict:
    """Two hex blocks sharing the x = nx*dx plane."""
    dx = 0.1
    gx = np.arange(2 * nx + 1, dtype=np.float64) * dx
    gy = np.arange(ny + 1, dtype=np.float64) * dx
    gz = np.arange(nz + 1, dtype=np.float64) * dx
    verts = np.array(
        [[x, y, z] for z in gz for y in gy for x in gx], dtype=np.float64
    )

    def vid(i: int, j: int, k: int) -> int:
        return int(k * (ny + 1) * (2 * nx + 1) + j * (2 * nx + 1) + i)

    n_cells = 2 * nx * ny * nz

    def cid(i: int, j: int, k: int) -> int:
        return int((k * ny + j) * (2 * nx) + i)

    faces = []  # (verts, owner, neigh or -1)
    for k in range(nz):
        for j in range(ny):
            for i in range(2 * nx):
                c = cid(i, j, k)
                v0 = vid(i, j, k)
                v1 = vid(i + 1, j, k)
                v2 = vid(i + 1, j + 1, k)
                v3 = vid(i, j + 1, k)
                v4 = vid(i, j, k + 1)
                v5 = vid(i + 1, j, k + 1)
                v6 = vid(i + 1, j + 1, k + 1)
                v7 = vid(i, j + 1, k + 1)
                # (vertices, neighbour id or None), outward normals
                cand = [
                    ((v0, v3, v2, v1), cid(i, j, k - 1) if k > 0 else None),
                    ((v4, v5, v6, v7), cid(i, j, k + 1) if k < nz - 1 else None),
                    ((v0, v1, v5, v4), cid(i, j - 1, k) if j > 0 else None),
                    ((v3, v7, v6, v2), cid(i, j + 1, k) if j < ny - 1 else None),
                    ((v0, v4, v7, v3), cid(i - 1, j, k) if i > 0 else None),
                    ((v1, v2, v6, v5), cid(i + 1, j, k) if i < 2 * nx - 1 else None),
                ]
                for fv, nb in cand:
                    if nb is not None and nb < c:
                        continue  # already added by the neighbour cell
                    faces.append((fv, c, nb if nb is not None else -1))

    n_faces = len(faces)
    npe = np.full(n_faces, 4, dtype=np.int64)
    conn = []
    owner = []
    neigh = []
    for fv, ow, nb in faces:
        conn.extend(v for v in fv)
        owner.append(ow)
        neigh.append(nb)
    face_offsets = np.zeros(n_faces + 1, dtype=np.int64)
    np.cumsum(npe, out=face_offsets[1:])
    owner_arr = np.asarray(owner, dtype=np.int64)
    neigh_arr = np.asarray(neigh, dtype=np.int64)

    # boundary faces grouped by block and side
    boundary_ids = np.flatnonzero(neigh_arr == -1)
    nA = nx * ny * nz
    in_a = owner_arr[boundary_ids] < nA

    def pick(mask, axis):
        ids = boundary_ids[mask]
        if axis == "xmin":
            return ids[np.mod(owner_arr[ids], 2 * nx) == 0]
        if axis == "xmax":
            return ids[np.mod(owner_arr[ids], 2 * nx) == 2 * nx - 1]
        if axis == "ymin":
            return ids[np.mod(owner_arr[ids] // (2 * nx), ny) == 0]
        if axis == "ymax":
            return ids[np.mod(owner_arr[ids] // (2 * nx), ny) == ny - 1]
        if axis == "zmin":
            return ids[owner_arr[ids] // (2 * nx * ny) == 0]
        if axis == "zmax":
            return ids[owner_arr[ids] // (2 * nx * ny) == nz - 1]
        return ids

    regions = []
    for block, mask, prefix in (
        ("air_domain", in_a, "open"),
        ("rotation1", ~in_a, "@PartSurface_rotation1"),
    ):
        for axis in ("xmin", "xmax", "ymin", "ymax", "zmin", "zmax"):
            ids = pick(mask, axis)
            if ids.size:
                regions.append((f"{prefix}_{axis}", ids))

    cvol = np.concatenate([
        np.full(nA, 1, dtype=np.int64),
        np.full(n_cells - nA, 3, dtype=np.int64),
    ])
    return {
        "vertices": verts,
        "n_vertices": len(verts),
        "link_data": {
            "n_faces": n_faces,
            "n_cells": n_cells,
            "npe": npe,
            "face_nodes": np.asarray(conn, dtype=np.int64),
            "face_offsets": face_offsets,
            "owner": owner_arr,
            "neighbor": neigh_arr,
            "boundary_faces": boundary_ids.tolist(),
        },
        "cvol_id": cvol,
        "parts_with_cvol": [("air_domain", 1), ("rotation1", 3)],
        "volume_regions": ["FluidRegion"],
        "surface_regions": regions,
    }


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("two_region.ccm")
    mesh = make_two_blocks()
    convert_model(
        mesh,
        out,
        title="two_region",
        split_regions=True,
        verbose=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
