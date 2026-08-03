"""Read a generated ``.ccm`` file back and sanity-check its topology."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np

from .ccmio import (
    CCMIO,
    K_CCMIO_BOUNDARY_FACES,
    K_CCMIO_BOUNDARY_REGION,
    K_CCMIO_CELL_TYPE,
    K_CCMIO_CELLS,
    K_CCMIO_INTERNAL_FACES,
    K_CCMIO_PROCESSOR,
    K_CCMIO_TOPOLOGY,
)


def _parse_stream(stream: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Split a CCM face stream into ``(n_verts_per_face, flat_vids, offsets)``."""
    if stream.size == 0:
        return (
            np.empty(0, np.int64),
            np.empty(0, np.int64),
            np.zeros(1, np.int64),
        )
    starts: list[int] = []
    i = 0
    n = int(stream.size)
    while i < n:
        starts.append(i)
        i += 1 + int(stream[i])
    starts_arr = np.asarray(starts, dtype=np.int64)
    npe_arr = stream[starts_arr].astype(np.int64)
    vids = np.empty(0, dtype=np.int64)
    if starts_arr.size:
        keep = np.ones(n, dtype=bool)
        keep[starts_arr] = False
        vids = stream[keep].astype(np.int64)
    return npe_arr, vids, starts_arr


def verify_ccm(path: str | Path, ccmio: Optional[CCMIO] = None, verbose: bool = True) -> dict:
    """Open *path*, read all mesh entities and check consistency.

    Returns a summary dict; raises ``AssertionError`` on any inconsistency.
    """
    ccmio = ccmio or CCMIO()
    path = Path(path)
    root = ccmio.open_file_readonly(str(path))
    try:
        state, problem = ccmio.get_state(root)
        processor = ccmio.next_entity(state, K_CCMIO_PROCESSOR, 0)
        if processor is None:
            raise AssertionError("no processor found in state")
        vertices_node, topology, _, _ = ccmio.read_processor(processor)

        # -- vertices ----------------------------------------------------
        dims, scale, _, coords = ccmio.read_vertices(vertices_node)
        n_verts = int(coords.shape[0])
        assert dims == 3, f"expected 3D vertices, got dims={dims}"
        assert np.isfinite(coords).all(), "non-finite vertex coordinates"

        # -- cells ---------------------------------------------------------
        cells_node = ccmio.get_entity(topology, K_CCMIO_CELLS, 0)
        _, cell_types = ccmio.read_cells(cells_node)
        n_cells = int(cell_types.size)
        assert n_cells > 0, "no cells"
        assert cell_types.min() >= 1, "cell type ids must be >= 1"

        # -- internal faces ------------------------------------------------
        internal_node = ccmio.get_entity(topology, K_CCMIO_INTERNAL_FACES, 0)
        n_if, _ = ccmio.entity_size(internal_node)
        n_if = int(n_if)
        internal_map, internal_stream = ccmio.read_faces(
            internal_node, K_CCMIO_INTERNAL_FACES
        )
        internal_cells = ccmio.read_face_cells(
            internal_node, K_CCMIO_INTERNAL_FACES
        )
        if n_if:
            iface_npe, iface_vids, _ = _parse_stream(internal_stream)
            assert iface_npe.size == n_if, "internal face count mismatch"
            assert iface_npe.min() >= 3, "internal face with < 3 vertices"
            assert iface_vids.min() >= 1 and iface_vids.max() <= n_verts, (
                "internal face vertex id out of range"
            )
            assert internal_cells.shape == (n_if, 2), "internal face-cells shape"
            assert internal_cells.min() >= 1 and internal_cells.max() <= n_cells, (
                "internal face owner/neighbour out of range"
            )
            assert (internal_cells[:, 0] != internal_cells[:, 1]).all(), (
                "internal face with identical owner/neighbour"
            )

        # -- boundary faces / regions ---------------------------------------
        boundary = {}
        # Enumerate indexed boundary-region entities (ids may start at 0 or 1)
        for node in ccmio.iter_entities(problem, K_CCMIO_BOUNDARY_REGION):
            region_id = ccmio.entity_index(node)
            label = ccmio.read_optstr(node, "Label")
            btype = ccmio.read_optstr(node, "BoundaryType")
            try:
                bfaces = ccmio.get_entity(topology, K_CCMIO_BOUNDARY_FACES, region_id)
                n_bf, _ = ccmio.entity_size(bfaces)
                bmap, bstream = ccmio.read_faces(bfaces, K_CCMIO_BOUNDARY_FACES)
                bcells = ccmio.read_face_cells(bfaces, K_CCMIO_BOUNDARY_FACES)
                if n_bf:
                    npe, vids, _ = _parse_stream(bstream)
                    assert npe.size == n_bf, f"region {label} face count mismatch"
                    assert vids.min() >= 1 and vids.max() <= n_verts
                    assert bcells.shape == (n_bf, 1)
                    assert bcells.min() >= 1 and bcells.max() <= n_cells
                    map_ids = ccmio.read_map(bmap, n_bf)
                else:
                    map_ids = np.empty(0, np.int64)
            except Exception:
                map_ids = np.empty(0, np.int64)
                n_bf = 0
            boundary[region_id] = {
                "label": label,
                "type": btype,
                "n_faces": int(n_bf),
                "map_ids": map_ids,
            }

        # boundary face ids must be unique across regions
        all_ids = np.concatenate(
            [b["map_ids"] for b in boundary.values()]
        ) if boundary else np.empty(0, np.int64)
        assert all_ids.size == np.unique(all_ids).size, (
            "duplicate boundary face ids across regions"
        )
        n_bf_total = int(all_ids.size)

        # cell types discovered from problem description
        cell_types_info = {}
        for node in ccmio.iter_entities(problem, K_CCMIO_CELL_TYPE):
            ctype_id = ccmio.entity_index(node)
            cell_types_info[ctype_id] = (
                ccmio.read_optstr(node, "Label"),
                ccmio.read_optstr(node, "MaterialType"),
            )
        used_types = set(int(v) for v in np.unique(cell_types))
        assert used_types <= set(cell_types_info), (
            f"cell type ids {used_types} missing from problem description "
            f"{set(cell_types_info)}"
        )

        summary = {
            "path": str(path),
            "version": ccmio.get_version(root),
            "n_vertices": n_verts,
            "n_cells": n_cells,
            "n_internal_faces": n_if,
            "n_boundary_faces": n_bf_total,
            "boundary_regions": {
                rid: {"label": b["label"], "type": b["type"], "n_faces": b["n_faces"]}
                for rid, b in boundary.items()
            },
            "cell_types": {
                cid: {"label": lbl, "material": mat}
                for cid, (lbl, mat) in cell_types_info.items()
            },
        }
        if verbose:
            print(f"[gph2ccm] verify OK: {summary}")
        return summary
    finally:
        ccmio.close_file(root)
