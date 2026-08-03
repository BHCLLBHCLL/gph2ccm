"""Generate a small Cartesian hexahedral mesh and write it as a .ccm file.

Used to validate that the gph2ccm CCM writer produces files STAR-CCM+ can
import and reorder (independent of the real Cradle GPH data).

Usage:  python tools/make_demo_ccm.py [nx ny nz] [output.ccm]
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gph2ccm.convert import convert_model  # noqa: E402


def make_hex_mesh(nx: int, ny: int, nz: int, dx: float = 0.01) -> dict:
    """Build a GPH-shaped mesh dict for a regular hexahedral block."""
    gx = np.arange(nx + 1, dtype=np.float64) * dx
    gy = np.arange(ny + 1, dtype=np.float64) * dx
    gz = np.arange(nz + 1, dtype=np.float64) * dx
    verts = np.array(
        [[x, y, z] for z in gz for y in gy for x in gx], dtype=np.float64
    )

    def vid(i: int, j: int, k: int) -> int:
        return int(k * (ny + 1) * (nx + 1) + j * (nx + 1) + i)

    face_records = []  # (verts[1-based], owner, neigh)
    n_cells = nx * ny * nz
    for k in range(nz):
        for j in range(ny):
            for i in range(nx):
                c = (k * ny + j) * nx + i
                v0, v1, v2, v3 = (
                    vid(i, j, k), vid(i + 1, j, k),
                    vid(i + 1, j + 1, k), vid(i, j + 1, k),
                )
                v4, v5, v6, v7 = (
                    vid(i, j, k + 1), vid(i + 1, j, k + 1),
                    vid(i + 1, j + 1, k + 1), vid(i, j + 1, k + 1),
                )
                # -z, +z, -y, +y, -x, +x (outward normals)
                faces = [
                    ([v0, v3, v2, v1], c, c - nx * ny if k > 0 else -1),
                    ([v4, v5, v6, v7], c, c + nx * ny if k < nz - 1 else -1),
                    ([v0, v1, v5, v4], c, c - nx if j > 0 else -1),
                    ([v3, v7, v6, v2], c, c + nx if j < ny - 1 else -1),
                    ([v0, v4, v7, v3], c, c - 1 if i > 0 else -1),
                    ([v1, v2, v6, v5], c, c + 1 if i < nx - 1 else -1),
                ]
                face_records.extend(faces)

    n_faces = len(face_records)
    npe = np.full(n_faces, 4, dtype=np.int64)
    conn = []
    owner = []
    neigh = []
    for fv, ow, nb in face_records:
        conn.extend(v + 1 for v in fv)
        owner.append(ow)
        neigh.append(nb)
    face_offsets = np.zeros(n_faces + 1, dtype=np.int64)
    np.cumsum(npe, out=face_offsets[1:])
    owner_arr = np.asarray(owner, dtype=np.int64)
    neigh_arr = np.asarray(neigh, dtype=np.int64)

    boundary_ids = np.flatnonzero(neigh_arr == -1)
    regions = []
    for name, axis in (
        ("xmin", 0), ("xmax", 1), ("ymin", 2), ("ymax", 3),
        ("zmin", 4), ("zmax", 5),
    ):
        ids = boundary_ids[axis::6]
        regions.append((name, ids))

    return {
        "vertices": verts,
        "n_vertices": len(verts),
        "link_data": {
            "n_faces": n_faces,
            "n_cells": n_cells,
            "npe": npe,
            "face_nodes": np.asarray(conn, dtype=np.int64) - 1,
            "face_offsets": face_offsets,
            "owner": owner_arr,
            "neighbor": neigh_arr,
            "boundary_faces": boundary_ids.tolist(),
        },
        "cvol_id": np.ones(n_cells, dtype=np.int64),
        "parts_with_cvol": [("fluid", 1)],
        "volume_regions": ["FluidRegion"],
        "surface_regions": regions,
    }


def main() -> int:
    nx = int(sys.argv[1]) if len(sys.argv) > 1 else 15
    ny = int(sys.argv[2]) if len(sys.argv) > 2 else nx
    nz = int(sys.argv[3]) if len(sys.argv) > 3 else nx
    out = Path(sys.argv[4]) if len(sys.argv) > 4 else Path("demo.ccm")
    mesh = make_hex_mesh(nx, ny, nz)
    convert_model(mesh, out, title="demo", verbose=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

