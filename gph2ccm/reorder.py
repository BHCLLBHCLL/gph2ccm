"""Cell renumbering utilities (reverse Cuthill-McKee on the dual graph)."""

from __future__ import annotations

from collections import deque

import numpy as np


def dual_adjacency(owner: np.ndarray, neigh: np.ndarray, n_cells: int):
    """Build CSR adjacency of the cell dual graph from face owner/neighbour."""
    internal = neigh >= 0
    a = np.concatenate([owner[internal], neigh[internal]]).astype(np.int64)
    b = np.concatenate([neigh[internal], owner[internal]]).astype(np.int64)
    keep = a != b  # self edges should not exist, guard anyway
    a = a[keep]
    b = b[keep]

    order = np.lexsort((b, a))
    a = a[order]
    b = b[order]
    is_new = np.empty(a.size, dtype=bool)
    is_new[0] = True
    np.not_equal(a[1:], a[:-1], out=is_new[1:])
    np.logical_or(is_new[1:], b[1:] != b[:-1], out=is_new[1:])
    a = a[is_new]
    b = b[is_new]
    indptr = np.zeros(n_cells + 1, dtype=np.int64)
    np.cumsum(np.bincount(a, minlength=n_cells), out=indptr[1:])
    return indptr, b


def rcm_order(owner: np.ndarray, neigh: np.ndarray, n_cells: int,
              boundary_cells: np.ndarray | None = None) -> np.ndarray:
    """Return a reverse Cuthill-McKee permutation (new_id[old_id])."""
    indptr, indices = dual_adjacency(owner, neigh, n_cells)
    degree = np.diff(indptr)

    if boundary_cells is not None and boundary_cells.size:
        starts = boundary_cells[np.argsort(degree[boundary_cells])]
    else:
        starts = np.array([int(np.argmin(degree))], dtype=np.int64)

    visited = np.zeros(n_cells, dtype=bool)
    order = np.empty(n_cells, dtype=np.int64)
    n_placed = 0
    for start in starts:
        if visited[start]:
            continue
        queue = deque([int(start)])
        visited[start] = True
        while queue:
            v = queue.popleft()
            order[n_placed] = v
            n_placed += 1
            nbr = indices[indptr[v] : indptr[v + 1]]
            nbr = nbr[~visited[nbr]]
            if nbr.size:
                nbr = nbr[np.argsort(degree[nbr], kind="stable")]
                for w in nbr:
                    if not visited[w]:
                        visited[w] = True
                        queue.append(int(w))
    # remaining isolated cells (should be none)
    rest = np.flatnonzero(~visited)
    if rest.size:
        order[n_placed : n_placed + rest.size] = rest
        n_placed += rest.size

    # reverse Cuthill-McKee: last visited cell gets the lowest new id
    perm = np.empty(n_cells, dtype=np.int64)
    new_ids = np.arange(n_cells, dtype=np.int64)
    perm[order] = new_ids[::-1]
    return perm


def apply_cell_order(mesh: dict, perm: np.ndarray) -> dict:
    """Renumber cells (and their cvol/cell-type data) using *perm*.

    ``perm[old_cell] = new_cell``.  Face order and vertex ids are unchanged,
    so surface-region face ids stay valid.
    """
    ld = mesh["link_data"]
    owner = np.asarray(ld["owner"], dtype=np.int64)
    neigh = np.asarray(ld["neighbor"], dtype=np.int64)
    n_cells = int(ld["n_cells"])

    new_owner = np.full(owner.shape, -1, dtype=np.int64)
    new_neigh = np.full(neigh.shape, -1, dtype=np.int64)
    valid_o = owner >= 0
    valid_n = neigh >= 0
    new_owner[valid_o] = perm[owner[valid_o]]
    new_neigh[valid_n] = perm[neigh[valid_n]]

    out = dict(mesh)
    ld_out = dict(ld)
    ld_out["owner"] = new_owner
    ld_out["neighbor"] = new_neigh
    if "cell_owner_faces" in ld_out:
        del ld_out["cell_owner_faces"]
    if "cell_neighbor_faces" in ld_out:
        del ld_out["cell_neighbor_faces"]
    out["link_data"] = ld_out

    cvol = mesh.get("cvol_id")
    if cvol is not None:
        inv = np.argsort(perm)  # inv[new] = old
        out["cvol_id"] = np.asarray(cvol, dtype=np.int64)[inv]
    return out
