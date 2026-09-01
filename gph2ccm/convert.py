"""GPH -> CCM orchestration."""

from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path
from typing import Optional

import numpy as np

from .ccmio import (
    CCMIO,
    K_CCMIO_BOUNDARY_FACES,
    K_CCMIO_BOUNDARY_REGION,
    K_CCMIO_CELLS,
    K_CCMIO_CELL_TYPE,
    K_CCMIO_INTERNAL_FACES,
    K_CCMIO_MAP,
    K_CCMIO_PROBLEM_DESCRIPTION,
    K_CCMIO_TOPOLOGY,
    K_CCMIO_VERTICES,
)
from .deps import import_gph2cgns
from .diagnose import diagnose_quality, format_findings
from .model import (
    CcmModel,
    build_model,
    boundary_face_cells,
    face_stream,
    internal_face_cells,
)
from .regions_schema import load_regions_checked

DEFAULT_CHUNK_FACES = 500_000


def load_regions(path: Optional[str | Path]) -> Optional[dict]:
    """Load the regions JSON and validate it against the gph2ccm schema.

    Every key is optional, so a misspelled key or a wrong type used to be
    ignored silently: the conversion finished, but the metadata never made it
    into the ``.ccm``.  Validating up front turns that into an actionable
    error (with line numbers) before any conversion work starts (B3).
    """
    if path is None:
        return None
    return load_regions_checked(path)


def load_boundary_types(path: Optional[str | Path]) -> dict[str, str]:
    if path is None:
        return {}
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"boundary-types JSON not found: {p}")
    with open(p, "r", encoding="utf-8") as f:
        data = json.load(f)
    return {str(k): str(v) for k, v in data.items()}


def parse_gph(gph_path: str | Path, verbose: bool = True) -> dict:
    gph2cgns = import_gph2cgns()
    t0 = time.perf_counter()
    if verbose:
        print(f"[gph2ccm] reading GPH: {gph_path}")
    mesh = gph2cgns.parse_gph_mesh(str(gph_path))
    if mesh.get("vertices") is None or mesh.get("link_data") is None:
        raise RuntimeError(f"failed to extract mesh from {gph_path}")
    ld = mesh["link_data"]
    if verbose:
        print(
            f"[gph2ccm] mesh: {mesh['n_vertices']} verts, "
            f"{ld['n_faces']} faces, {ld['n_cells']} cells, "
            f"{len(ld['boundary_faces'])} boundary faces "
            f"[{time.perf_counter() - t0:.1f}s]"
        )
    return mesh


def _face_starts(ld: dict, face_ids: np.ndarray) -> np.ndarray:
    """Element offset of every face inside its CCM vertex stream."""
    n = int(face_ids.size)
    starts = np.empty(n, dtype=np.int64)
    if n == 0:
        return starts
    starts[0] = 0
    if n > 1:
        np.cumsum(np.asarray(ld["npe"], dtype=np.int64)[face_ids[:-1]] + 1,
                  out=starts[1:])
    return starts


def face_centroids_and_normals(ld: dict, vertices: np.ndarray):
    """Per-face centroid and signed area vector (Newell) for every GPH face."""
    npe = np.asarray(ld["npe"], dtype=np.int64)
    offs = np.asarray(ld["face_offsets"], dtype=np.int64)
    fn = np.asarray(ld["face_nodes"], dtype=np.int64)
    coords = np.asarray(vertices, dtype=np.float64)
    p = coords[fn]
    n = int(ld["n_faces"])
    face_len = np.repeat(npe, npe)
    face_start = np.repeat(offs[:-1], npe)
    pos = np.arange(fn.size) - face_start
    nxt_pos = np.where(pos + 1 < face_len, pos + 1, 0)
    q = coords[fn[face_start + nxt_pos]]
    cross = np.cross(p, q)
    area_vec = 0.5 * np.add.reduceat(cross, offs[:-1])
    fc = np.add.reduceat(p, offs[:-1]) / npe[:, None]
    return fc, area_vec


def cell_centroids(
    ld: dict, n_cells: int, face_centroids: np.ndarray, area_vec: np.ndarray
) -> np.ndarray:
    """Cell centroid via the divergence theorem (area-weighted, A3 / L3).

    For a closed polyhedron with outward area vectors ``S_f`` and face
    centroids ``c_f``::

        V = (1/3) sum_f c_f . S_f
        C = (1/(4V)) sum_f (c_f . S_f) c_f

    Both sums are origin-independent and weight each face by its area, so a
    sliver face can no longer drag the centroid the way the previous
    arithmetic mean of face centroids did -- that mean is what mis-oriented
    interface normals on distorted cut cells (issue L3).

    ``area_vec`` is the signed area vector of the GPH winding, which points
    from owner to neighbour: outward for the owner cell, inward for the
    neighbour.  Cells with a degenerate/zero volume fall back to the old
    arithmetic mean so open or pathological cells still get a usable point.
    """
    owner = np.asarray(ld["owner"], dtype=np.int64)
    neigh = np.asarray(ld["neighbor"], dtype=np.int64)
    valid = neigh >= 0

    # c_f . S_f with S_f outward for the owner, inward for the neighbour.
    dot = np.einsum("ij,ij->i", face_centroids, area_vec)

    wsum = np.zeros((n_cells, 3))
    np.add.at(wsum, owner, face_centroids * dot[:, None])
    np.add.at(wsum, neigh[valid], -(face_centroids[valid] * dot[valid, None]))
    vsum = np.zeros(n_cells)
    np.add.at(vsum, owner, dot)
    np.add.at(vsum, neigh[valid], -dot[valid])
    vol = vsum / 3.0

    with np.errstate(divide="ignore", invalid="ignore"):
        weighted = wsum / (4.0 * vol)[:, None]
    good = np.isfinite(weighted).all(axis=1) & (vol > 0)

    if good.all():
        return weighted

    # Fallback: arithmetic mean of face centroids (previous behaviour) for
    # zero/negative-volume or non-finite cells.
    csum = np.zeros((n_cells, 3))
    np.add.at(csum, owner, face_centroids)
    np.add.at(csum, neigh[valid], face_centroids[valid])
    cnt = np.bincount(
        np.concatenate([owner, neigh[valid]]), minlength=n_cells
    )
    fallback = csum / np.maximum(cnt[:, None], 1)
    out = weighted.copy()
    out[~good] = fallback[~good]
    return out


def orient_interface_streams(
    stream: np.ndarray,
    face_ids: np.ndarray,
    ld: dict,
    owners_a: np.ndarray,
    owners_b: np.ndarray,
    centroids: np.ndarray,
    area_vec: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Return two face streams oriented outward from side A / side B.

    Side A's copy has its normal pointing from cell A toward cell B, side B's
    copy points the opposite way.  Faces whose GPH winding is already correct
    are kept unchanged; the other side is reversed.
    """
    face_ids = np.asarray(face_ids, dtype=np.int64)
    d = centroids[owners_b] - centroids[owners_a]
    dot = np.einsum("ij,ij->i", area_vec[face_ids], d)
    rev_a = dot < 0
    rev_b = dot > 0

    def _rev(mask: np.ndarray) -> np.ndarray:
        out = stream.copy()
        npe = np.asarray(ld["npe"], dtype=np.int64)[face_ids]
        offs = _face_starts(ld, face_ids)
        for i in np.flatnonzero(mask):
            s = int(offs[i])
            e = s + 1 + int(npe[i])
            seg = out[s + 1 : e]
            out[s + 1 : e] = seg[::-1]
        return out

    return _rev(rev_a), _rev(rev_b)


def _short_label(label: str) -> str:
    """Shorten a cell-type label for 32-char CCM boundary labels."""
    s = label.replace("_domain", "")
    return s[:16]


# ---------------------------------------------------------------------------
# C1: periodic / sliding pairings -- geometry validation & effective write.
# ---------------------------------------------------------------------------


def _find_boundary_region(
    regions: list["BoundaryRegion"], label: str
) -> Optional["BoundaryRegion"]:
    """Locate a boundary region by exact or dotted-suffix label match."""
    for r in regions:
        if r.label == label:
            return r
        if label.endswith("." + r.label) or r.label.endswith("." + label):
            return r
    return None


def _parse_vec3(text: str) -> Optional[np.ndarray]:
    """Parse ``"0 0 1"`` / ``"0,0,1"`` into a length-3 float array."""
    if not text:
        return None
    parts = [p for p in re.split(r"[,\s;]+", str(text).strip()) if p]
    if len(parts) != 3:
        return None
    try:
        return np.asarray([float(p) for p in parts], dtype=np.float64)
    except ValueError:
        return None


def _face_unique_vertices(ld: dict, face_ids, vertices: np.ndarray) -> np.ndarray:
    """Unique vertex coordinates (rounded, deduplicated) of a face subset."""
    fids = np.asarray(face_ids, dtype=np.int64)
    if fids.size == 0:
        return np.empty((0, 3))
    npe = np.asarray(ld["npe"], dtype=np.int64)[fids]
    offs = np.asarray(ld["face_offsets"], dtype=np.int64)[fids]
    fn = np.asarray(ld["face_nodes"], dtype=np.int64)
    vids = np.concatenate([fn[o : o + n] for o, n in zip(offs, npe)])
    return np.unique(np.round(vertices[vids] * 1e6) / 1e6, axis=0)


def _sets_match_translate(va: np.ndarray, vb: np.ndarray, vec: np.ndarray,
                          tol: float = 1e-4) -> bool:
    """Multiset equality of ``va + vec`` vs ``vb`` up to rounding tolerance."""
    moved = np.round((va + vec) / tol)
    target = np.round(vb / tol)
    ua, ca = np.unique(moved, axis=0, return_counts=True)
    ub, cb = np.unique(target, axis=0, return_counts=True)
    return (
        ua.shape == ub.shape
        and np.array_equal(ua, ub)
        and np.array_equal(ca, cb)
    )


def _sets_congruent(va: np.ndarray, vb: np.ndarray, tol: float = 1e-4) -> bool:
    """Rigid-motion congruence (necessary condition) of two point sets.

    Uses the multiset of all pairwise distances, which is invariant under
    every rigid motion (rotation about any axis, translation).  O(n^2); the
    caller caps the vertex count to keep large periodic faces cheap.
    """
    if va.shape[0] != vb.shape[0]:
        return False

    def _dist_multiset(pts: np.ndarray) -> np.ndarray:
        d = np.linalg.norm(pts[:, None, :] - pts[None, :, :], axis=2)
        iu = np.triu_indices(pts.shape[0], k=1)
        return np.sort(np.round(d[iu] / tol))

    try:
        return np.array_equal(_dist_multiset(va), _dist_multiset(vb))
    except MemoryError:
        return True  # too big to check cheaply -- rely on counts


_MAX_DISTANCE_CHECK_VERTS = 600


def periodic_pair_errors(model: "CcmModel", ld: dict) -> list[str]:
    """Validate every periodic pair that references *existing* regions.

    Returns a list of human-readable problems (empty = all checked pairs
    pass).  Pairs whose region/shadow does not exist in the mesh are skipped
    here -- the caller treats them as a warning and keeps the pairing
    descriptive only.  Pairs that reference real regions must satisfy:

    * equal face counts;
    * congruent vertex geometry -- exact under the documented translation
      vector (translational type), or rigid-motion congruence (rotational
      type, checked via the pairwise-distance multiset up to a size cap).
    """
    errors: list[str] = []
    for p in model.periodic or []:
        if not isinstance(p, dict):
            continue
        name = p.get("name") or "?"
        region = p.get("region") or ""
        shadow = p.get("shadow") or ""
        ra = _find_boundary_region(model.boundary_regions, region)
        rb = _find_boundary_region(model.boundary_regions, shadow)
        if ra is None or rb is None:
            continue  # caller warns; keep descriptive
        if ra.face_ids.size != rb.face_ids.size:
            errors.append(
                f"{name}: '{region}' has {ra.face_ids.size} faces but "
                f"'{shadow}' has {rb.face_ids.size}"
            )
            continue
        va = _face_unique_vertices(ld, ra.face_ids, model.vertices)
        vb = _face_unique_vertices(ld, rb.face_ids, model.vertices)
        if va.shape[0] != vb.shape[0]:
            errors.append(
                f"{name}: '{region}' has {va.shape[0]} boundary vertices but "
                f"'{shadow}' has {vb.shape[0]}"
            )
            continue
        ptype = str(p.get("type") or "rotational").lower()
        vec = _parse_vec3(p.get("translation") or p.get("angle") or "")
        if ptype == "translational" and vec is not None:
            if not _sets_match_translate(va, vb, vec):
                errors.append(
                    f"{name}: '{region}' and '{shadow}' do not match under "
                    f"translation ({vec[0]:g} {vec[1]:g} {vec[2]:g})"
                )
        elif va.shape[0] <= _MAX_DISTANCE_CHECK_VERTS and not _sets_congruent(va, vb):
            errors.append(
                f"{name}: '{region}' and '{shadow}' are not congruent "
                f"(rigid-motion check failed)"
            )
    return errors


class CcmMeshWriter:
    """Write a :class:`CcmModel` to a legacy ``.ccm`` file via CCMIO."""

    def __init__(
        self,
        ccmio: CCMIO,
        out_path: str | Path,
        *,
        title: Optional[str] = None,
        chunk_faces: int = DEFAULT_CHUNK_FACES,
        cell_topology: Optional[str] = None,
        split_regions: bool = False,
        verbose: bool = True,
    ):
        self.ccmio = ccmio
        self.out_path = Path(out_path)
        self.title = title
        self.chunk_faces = chunk_faces
        self.cell_topology = cell_topology
        self.split_regions = split_regions
        self.verbose = verbose

    def _log(self, msg: str) -> None:
        if self.verbose:
            print(msg, flush=True)

    def _add_map(self, root, name: str, data: np.ndarray) -> "CCMIOID":
        map_id = self.ccmio.new_entity(root, K_CCMIO_MAP, name)
        self.ccmio.write_map(map_id, data, int(data.max()) if data.size else 0)
        return map_id

    def _write_vertices(self, node, map_id, vertices_mm: np.ndarray) -> None:
        # NOTE: the CCMIO 2D block-write path used by the STAR-CCM+ ccmio.dll
        # misplaces chunks: for a [3][n] array it treats start/end as flat
        # element offsets, so a chunk beginning at vertex s lands at offset s
        # instead of 3*s.  Write the array in one call (matches the libccmio
        # reference writer) to avoid corrupting vertex coordinates.
        flat = np.ascontiguousarray(vertices_mm.reshape(-1))
        self.ccmio.write_vertices(node, map_id, flat, 0.001, 0, None)

    def _write_face_group(
        self,
        node,
        which: int,
        map_id,
        ld: dict,
        face_ids: np.ndarray,
        stream: np.ndarray,
        cells: np.ndarray,
        with_cells: bool = True,
    ) -> None:
        # NOTE: same ccmio.dll limitation as _write_vertices.  The face
        # vertex stream is 1-D so chunked writes are safe, but the internal
        # face-cells array is [2][n] and chunked writes land at half the
        # intended offset.  Write the whole face-cells array in one call.
        if with_cells and cells.size:
            self.ccmio.write_face_cells(node, which, map_id, cells)
        n = int(face_ids.size)
        if n == 0:
            return
        starts = _face_starts(ld, face_ids)
        for i0 in range(0, n, self.chunk_faces):
            i1 = min(n, i0 + self.chunk_faces)
            e0 = int(starts[i0])
            e1 = int(starts[i1]) if i1 < n else int(stream.size)
            self.ccmio.write_faces(
                node, which, map_id, int(stream.size), stream[e0:e1], e0, e1
            )

    def _write_boundary_patch(
        self,
        root,
        topology,
        problem,
        region_id: int,
        map_name: str,
        label: str,
        ld: dict,
        face_ids: np.ndarray,
        stream: np.ndarray,
        cells: Optional[np.ndarray],
        map_values: Optional[np.ndarray] = None,
    ) -> None:
        map_data = (
            np.asarray(map_values, dtype=np.int32)
            if map_values is not None
            else (np.asarray(face_ids, dtype=np.int64) + 1).astype(np.int32)
        )
        region_map = self._add_map(root, map_name, map_data)
        region_node = self.ccmio.new_indexed_entity(
            topology, K_CCMIO_BOUNDARY_FACES, region_id, label
        )
        self._write_face_group(
            region_node,
            K_CCMIO_BOUNDARY_FACES,
            region_map,
            ld,
            face_ids,
            stream,
            cells if cells is not None else np.empty(0, np.int32),
            with_cells=cells is not None,
        )
        pnode = self.ccmio.new_indexed_entity(
            problem, K_CCMIO_BOUNDARY_REGION, region_id, label
        )
        self.ccmio.write_optstr(pnode, "Label", label[:32])
        self.ccmio.write_optstr(pnode, "BoundaryType", "wall")

    def _write_region_cell_maps(self, root, model: CcmModel) -> None:
        for ct in model.cell_table:
            cell_idx = np.flatnonzero(model.cell_types == ct.id)
            if cell_idx.size == 0:
                continue
            map_data = (cell_idx + 1).astype(np.int32)
            map_id = self.ccmio.new_entity(
                root, K_CCMIO_MAP, f"Region Cell Map {ct.label}"
            )
            self.ccmio.write_map(map_id, map_data, int(map_data.max()))

    def _write_interfaces(self, root, topology, problem, model, ld) -> None:
        if not model.interface_faces:
            return
        self._log(
            f"[gph2ccm] writing {len(model.interface_faces)} region interface(s) ..."
        )
        fc, area_vec = face_centroids_and_normals(ld, model.vertices)
        centroids = cell_centroids(ld, model.n_cells, fc, area_vec)
        owner_all = np.asarray(ld["owner"], dtype=np.int64)
        neigh_all = np.asarray(ld["neighbor"], dtype=np.int64)
        n_faces = int(ld["n_faces"])

        used_ids = {0}
        used_ids.update(r.id for r in model.boundary_regions)
        rid = max(used_ids) + 1
        records = []

        for k, (label_a, label_b, fids) in enumerate(
            model.interface_faces, start=1
        ):
            fids = np.asarray(fids, dtype=np.int64)
            short_a = _short_label(label_a)
            short_b = _short_label(label_b)
            base_a = f"{short_a}_to_{short_b}"
            base_b = f"{short_b}_to_{short_a}"
            ct_a = next(ct.id for ct in model.cell_table if ct.label == label_a)
            owner = owner_all[fids]
            neigh = neigh_all[fids]
            side_a = np.where(model.cell_types[owner] == ct_a, owner, neigh)
            side_b = np.where(model.cell_types[owner] == ct_a, neigh, owner)
            stream = face_stream(ld, fids)
            a_stream, b_stream = orient_interface_streams(
                stream, fids, ld, side_a, side_b, centroids, area_vec
            )
            self._log(
                f"[gph2ccm]   interface {k}: {label_a} <-> {label_b} "
                f"({fids.size} faces)"
            )
            b0, b1 = rid, rid + 1

            # per-side volume patches (close the cells, carry owner data)
            self._write_boundary_patch(
                root, topology, problem, b0,
                f"Boundary Face Map {label_a}:{base_a}", base_a,
                ld, fids, a_stream, side_a + 1,
            )
            self._write_boundary_patch(
                root, topology, problem, b1,
                f"Boundary Face Map {label_b}:{base_b}", base_b,
                ld, fids, b_stream, side_b + 1,
            )
            rid = b1 + 1

            # grid-interface surface pair (no cell data), one per side
            self._write_boundary_patch(
                root, topology, problem, rid,
                f"Boundary Face Map {label_a}:{base_a} [Interface {k}]",
                f"{base_a} [Interface {k}]",
                ld, fids, a_stream, None,
                map_values=fids + 1 + n_faces,
            )
            rid += 1
            self._write_boundary_patch(
                root, topology, problem, rid,
                f"Boundary Face Map {label_b}:{base_b} [Interface {k}]",
                f"{base_b} [Interface {k}]",
                ld, fids, b_stream, None,
                map_values=fids + 1 + 2 * n_faces,
            )
            rid += 1

            records.append((f"Interface {k}", b0, b1))

        self._write_interface_definitions(root, records, model)

    def _write_interface_definitions(self, root, records, model) -> None:
        """Write the STAR-CCM+ ``InterfaceDefinitions`` node (root child).

        Mirrors the structure written by STAR-CCM+ itself (see
        ``bladerotating_dm2.ccm``): one ``Interface-N`` node per interface
        with ``Name``, ``Boundary0``/``Boundary1`` (boundary-region ids of
        the two per-side patches), ``Configuration`` and ``ConditionType``.

        *records* are the split-mode grid interfaces (written as
        ``InternalInterface``); user-declared ``model.periodic`` pairings that
        reference existing boundary regions are appended as
        ``PeriodicInterface`` (C1 -- effective, not just descriptive).
        """
        # -- user-declared periodic pairings ------------------------------
        periodic_pairs: list[tuple[str, int, int]] = []
        for p in model.periodic or []:
            if not isinstance(p, dict):
                continue
            name = p.get("name")
            if not name:
                continue
            ra = _find_boundary_region(
                model.boundary_regions, p.get("region") or ""
            )
            rb = _find_boundary_region(
                model.boundary_regions, p.get("shadow") or ""
            )
            if ra is None or rb is None:
                self._log(
                    f"[gph2ccm]   periodic '{name}': boundary region "
                    f"'{p.get('region')}'/'{p.get('shadow')}' not found -- "
                    f"keeping the pairing descriptive only"
                )
                continue
            periodic_pairs.append((str(name), ra.id, rb.id))
            self._log(
                f"[gph2ccm]   periodic '{name}': writing effective interface "
                f"between regions {ra.id} ('{ra.label}') and "
                f"{rb.id} ('{rb.label}')"
            )

        if not records and not periodic_pairs:
            return
        idf = self.ccmio.create_node(
            root.node, "InterfaceDefinitions", "InterfaceDefinitions"
        )
        for k, (name, b0, b1) in enumerate(records):
            iface = self.ccmio.create_node(idf, f"Interface-{k}", "Interface")
            self.ccmio.write_nodestr(iface, "Name", name)
            self.ccmio.write_nodei(iface, "Boundary0", b0)
            self.ccmio.write_nodei(iface, "Boundary1", b1)
            self.ccmio.write_nodestr(iface, "Configuration", "IN_PLACE")
            self.ccmio.write_nodestr(
                iface, "ConditionType", "InternalInterface"
            )
        for k, (name, b0, b1) in enumerate(periodic_pairs, start=len(records)):
            iface = self.ccmio.create_node(idf, f"Interface-{k}", "Interface")
            self.ccmio.write_nodestr(iface, "Name", name)
            self.ccmio.write_nodei(iface, "Boundary0", b0)
            self.ccmio.write_nodei(iface, "Boundary1", b1)
            self.ccmio.write_nodestr(iface, "Configuration", "IN_PLACE")
            self.ccmio.write_nodestr(
                iface, "ConditionType", "PeriodicInterface"
            )

    def _write_metadata_nodes(self, problem, model: CcmModel) -> None:
        """Write optional, data-driven descriptive metadata as namespaced opt
        nodes on the problem description.

        The following ``model`` attributes are carried over (all optional,
        user-supplied via the regions JSON, never auto-applied):

        * ``fields``         -> ``gph2ccm.Field.<name>``  (``"<loc>|<type>|<units>"``)
        * ``solver_settings``-> ``gph2ccm.Solver.<key>``  (``str(value)``)
        * ``mrf``            -> ``gph2ccm.MRF.<name>``    (rotating reference frame)
        * ``periodic``      -> ``gph2ccm.Periodic.<name>`` (interface pairing)

        Each group also gets a ``*Names`` / ``*Keys`` index list.  This is
        reference metadata only -- gph2ccm never writes actual solution data
        or turns itself into a solver-ready exporter (keep-boundary scope).
        """
        if not (model.fields or model.solver_settings or model.mrf or model.periodic):
            return
        self._log("[gph2ccm] writing descriptive field/solver metadata ...")

        field_names: list[str] = []
        for f in model.fields:
            if not isinstance(f, dict):
                continue
            name = f.get("name")
            if not name:
                continue
            loc = f.get("location", "cell")
            dtype = f.get("type", "scalar")
            units = f.get("units", "")
            field_names.append(str(name))
            self.ccmio.write_optstr(
                problem, f"gph2ccm.Field.{name}", f"{loc}|{dtype}|{units}"
            )
        if field_names:
            self.ccmio.write_optstr(problem, "gph2ccm.FieldNames", ",".join(field_names))

        solver_keys: list[str] = []
        for k, v in model.solver_settings.items():
            solver_keys.append(str(k))
            self.ccmio.write_optstr(problem, f"gph2ccm.Solver.{k}", str(v))
        if solver_keys:
            self.ccmio.write_optstr(
                problem, "gph2ccm.SolverKeys", ",".join(solver_keys)
            )

        # -- rotating reference frames (descriptive) -------------------------
        mrf_names: list[str] = []
        for fr in model.mrf:
            if not isinstance(fr, dict):
                continue
            name = fr.get("name")
            if not name:
                continue
            region = fr.get("region", "")
            ftype = fr.get("type", "rotating")
            axis = fr.get("axis", "")
            origin = fr.get("origin", "")
            omega = fr.get("omega", "")
            units = fr.get("units", "")
            mrf_names.append(str(name))
            self.ccmio.write_optstr(
                problem, f"gph2ccm.MRF.{name}",
                f"{region}|{ftype}|{axis}|{origin}|{omega}|{units}",
            )
        if mrf_names:
            self.ccmio.write_optstr(problem, "gph2ccm.MRFNames", ",".join(mrf_names))

        # -- periodic / cyclic / sliding pairings (descriptive) --------------
        per_names: list[str] = []
        for p in model.periodic:
            if not isinstance(p, dict):
                continue
            name = p.get("name")
            if not name:
                continue
            region = p.get("region", "")
            shadow = p.get("shadow", "")
            ptype = p.get("type", "rotational")
            axis = p.get("axis", "")
            angle = p.get("angle", p.get("translation", ""))
            per_names.append(str(name))
            self.ccmio.write_optstr(
                problem, f"gph2ccm.Periodic.{name}",
                f"{region}|{shadow}|{ptype}|{axis}|{angle}",
            )
        if per_names:
            self.ccmio.write_optstr(
                problem, "gph2ccm.PeriodicNames", ",".join(per_names)
            )

    def _write_processor_note(self, problem, model: CcmModel) -> None:
        """Record the legacy-CCM single-processor limitation as descriptive
        metadata and warn when a partitioned write would normally be expected.

        The legacy ``.ccm`` format written by libccmio is single-processor:
        there is exactly one ``K_CCMIO_PROCESSOR`` entity per file, so gph2ccm
        cannot emit the distributed / multi-partition layout that very large
        meshes (or STAR-CCM+ parallel runs) would use.  We carry that fact in
        the file so the limitation is self-documenting rather than silent.
        """
        self.ccmio.write_optstr(problem, "gph2ccm.Note.Processors", "1")
        self.ccmio.write_optstr(
            problem, "gph2ccm.Note.MultiProcessor", "unsupported"
        )
        # Heuristic threshold beyond which a partitioned write is usually wanted.
        if model.n_cells > 2_000_000:
            self._log(
                "[gph2ccm] note: legacy CCM is single-processor; this mesh "
                f"({model.n_cells} cells) is written as one processor. Very "
                "large meshes may hit format/tooling limits -- consider "
                "splitting upstream or importing in chunks."
            )

    def _write_dimension_note(self, problem, model: CcmModel) -> None:
        """Detect a 2D (collapsed-axis) mesh and record that legacy-CCM 2D
        wrapping is not performed.

        gph2ccm never extrudes a shell layer to make a 2D Cradle mesh valid in
        STAR-CCM+'s 2D mode (out of the "keep boundary" scope -- it would be
        modifying the mesh, not merely describing it).  We detect a collapsed
        axis and carry the fact plus the limitation in the file so the user is
        not surprised at import time.
        """
        verts = np.asarray(model.vertices, dtype=np.float64)
        if verts.shape[0] == 0:
            return
        ext = verts.max(axis=0) - verts.min(axis=0)
        scale = float(np.max(ext)) if ext.size else 0.0
        eps = max(scale, 1e-9) * 1e-6
        n_active = int(np.count_nonzero(ext > eps))
        # A genuinely 3D mesh has 3 active axes; a 2D mesh has exactly 2.
        ndim = 3 if n_active >= 3 else 2
        self.ccmio.write_optstr(problem, "gph2ccm.Note.Dimension", f"{ndim}D")
        if ndim < 3:
            self.ccmio.write_optstr(
                problem, "gph2ccm.Note.TwoDWrapping", "unsupported"
            )
            self._log(
                "[gph2ccm] note: mesh appears 2D (a collapsed axis); legacy "
                "CCM 2D wrapping (shell-layer extrusion) is NOT performed. "
                "Import as 2D in STAR-CCM+ manually if required."
            )
        else:
            self.ccmio.write_optstr(problem, "gph2ccm.Note.TwoDWrapping", "n/a")

    def _write_quality_note(self, problem, model: CcmModel, ld: dict) -> None:
        """Embed a read-only quality summary as descriptive ``gph2ccm.Quality.*``
        nodes (gph2ccm never modifies the mesh -- diagnostic only).

        Surfaces the two issues that are cheap to detect at export time: boundary
        faces left in ``Default_Boundary_Region`` (uncovered) and degenerate
        boundary faces (``npe < 3``).  Heavier checks (duplicate faces, cell
        closure) remain in ``tools/topo_check.py``.
        """
        q = diagnose_quality(model, ld)
        # Node names are capped at K_CCMIO_MAX_STRING_LENGTH (32); use the
        # short "gph2ccm.Qual." prefix so every key fits.
        self.ccmio.write_optstr(
            problem, "gph2ccm.Qual.Summary", "ok" if q["ok"] else "issues"
        )
        self.ccmio.write_optstr(
            problem,
            "gph2ccm.Qual.Severity",
            "error" if q["has_errors"] else ("ok" if q["ok"] else "warning"),
        )
        self.ccmio.write_optstr(
            problem,
            "gph2ccm.Qual.Uncovered",
            str(q["n_uncovered_boundary"]),
        )
        self.ccmio.write_optstr(
            problem,
            "gph2ccm.Qual.Degenerate",
            str(q["n_degenerate_boundary"]),
        )
        if q["issues"]:
            self.ccmio.write_optstr(
                problem, "gph2ccm.Qual.Issues", " | ".join(q["issues"])
            )
            # B4: graded findings with fix hints, written alongside the raw
            # issues so the post-import checklist is actionable.
            hints = [f["hint"] for f in q.get("findings", []) if f.get("hint")]
            if hints:
                self.ccmio.write_optstr(
                    problem, "gph2ccm.Qual.Hints", " | ".join(hints)
                )
            self._log("[gph2ccm] quality notes: " + " | ".join(q["issues"]))
            for line in format_findings(q):
                self._log("[gph2ccm]   " + line)

    def write(self, model: CcmModel, ld: dict) -> None:
        ccmio = self.ccmio
        out = self.out_path

        # C1: periodic pairings become *effective* only when their geometry
        # actually matches -- a declared pair that cannot hold is an error
        # (fail fast, before any output exists), never a silent bad write.
        if model.periodic:
            problems = periodic_pair_errors(model, ld)
            if problems:
                raise ValueError(
                    "periodic pairings fail geometry validation:\n  - "
                    + "\n  - ".join(problems)
                )

        if out.exists():
            if self.verbose:
                print(f"[gph2ccm] removing existing output: {out}")
            out.unlink()

        t0 = time.perf_counter()
        root = ccmio.open_file(out)
        try:
            self._write_entities(root, model, ld)
        except BaseException:
            # M4: never leak the CCMIO file handle, and never leave a
            # half-written, unreadable .ccm behind when a write fails.
            self._discard_output(root, out)
            raise
        ccmio.close_file(root)

        self._log(
            f"[gph2ccm] wrote {out} "
            f"[{time.perf_counter() - t0:.1f}s]"
        )

    def _discard_output(self, root, out: Path) -> None:
        """Best-effort cleanup after a failed write (M4).

        Closes the CCMIO file and removes the partial output so the user
        never sees a corrupt ``.ccm``.  Cleanup errors are logged, not
        raised -- the original failure must stay visible.
        """
        try:
            self.ccmio.close_file(root)
        except Exception as exc:  # noqa: BLE001 - cleanup must not mask the cause
            self._log(f"[gph2ccm] warning: closing failed output: {exc}")
        try:
            out.unlink()
        except OSError as exc:
            self._log(f"[gph2ccm] warning: cannot remove partial output: {exc}")
        else:
            self._log(f"[gph2ccm] removed partial output: {out}")

    def _write_entities(self, root, model: CcmModel, ld: dict) -> None:
        """Write every CCM entity; the caller owns open/close of ``root``."""
        ccmio = self.ccmio
        if self.title:
            ccmio.set_title(root, self.title)

        state = ccmio.new_state(root, "default", "gph2ccm")
        processor = ccmio.new_processor(state)
        ccmio.clear_processor(state, processor)
        problem = ccmio.new_entity(root, K_CCMIO_PROBLEM_DESCRIPTION, "gph2ccm mesh")

        if self.split_regions:
            self._write_region_cell_maps(root, model)

        # -- vertices -----------------------------------------------------
        self._log("[gph2ccm] writing vertices ...")
        vertex_map = self._add_map(
            root, "Vertex map", np.arange(1, model.vertices.shape[0] + 1, dtype=np.int32)
        )
        vertices_node = ccmio.new_entity(root, K_CCMIO_VERTICES, "Vertices")
        vertices_mm = model.vertices.astype(np.float32) * 1000.0
        self._write_vertices(vertices_node, vertex_map, vertices_mm)

        # -- cells ---------------------------------------------------------
        self._log(f"[gph2ccm] writing cells ({model.n_cells}) ...")
        cell_map = self._add_map(
            root, "Cell map", np.arange(1, model.n_cells + 1, dtype=np.int32)
        )
        topology = ccmio.new_entity(root, K_CCMIO_TOPOLOGY, "Mesh")
        cells_node = ccmio.new_entity(topology, K_CCMIO_CELLS, "Cells")
        ccmio.write_cells(cells_node, cell_map, model.cell_types)
        if self.cell_topology:
            if self.cell_topology not in ("poly", "auto"):
                raise ValueError(
                    f"unsupported cell-topology mode: {self.cell_topology}"
                )
            # Legacy CCM files written by STAR-CCM+ carry an explicit
            # CellTopologyType so the importer does not have to detect every
            # cell shape.  Cradle cut-cell meshes are general polyhedra
            # (PROSTAR shape 255 = polyhedral) even when they look hexahedral.
            topology_values = np.full(model.n_cells, 255, dtype=np.int32)
            self._log("[gph2ccm] writing CellTopologyType (polyhedral) ...")
            for s in range(0, model.n_cells, self.chunk_faces):
                e = min(model.n_cells, s + self.chunk_faces)
                ccmio.write_opt1i(
                    cells_node,
                    "CellTopologyType",
                    topology_values[s:e],
                    model.n_cells,
                    s,
                    e,
                )

        # -- internal faces ------------------------------------------------
        n_internal = int(model.internal_face_ids.size)
        if n_internal:
            self._log(f"[gph2ccm] writing internal faces ({n_internal}) ...")
            internal_map = self._add_map(
                root,
                "InternalFaces map",
                (model.internal_face_ids + 1).astype(np.int32),
            )
            internal_node = ccmio.new_entity(
                topology, K_CCMIO_INTERNAL_FACES, "Internal faces"
            )
            stream = face_stream(ld, model.internal_face_ids)
            cells = internal_face_cells(ld, model.internal_face_ids)
            self._write_face_group(
                internal_node,
                K_CCMIO_INTERNAL_FACES,
                internal_map,
                ld,
                model.internal_face_ids,
                stream,
                cells,
            )

        # -- boundary faces ------------------------------------------------
        regions = list(model.boundary_regions)
        if model.default_face_ids.size:
            self._log(
                f"[gph2ccm] writing Default_Boundary_Region "
                f"({model.default_face_ids.size} faces) ..."
            )
            default_map = self._add_map(
                root,
                "boundaryMap-0",
                (model.default_face_ids + 1).astype(np.int32),
            )
            default_node = ccmio.new_indexed_entity(
                topology, K_CCMIO_BOUNDARY_FACES, 0, "Default_Boundary_Region"
            )
            stream = face_stream(ld, model.default_face_ids)
            cells = boundary_face_cells(ld, model.default_face_ids)
            self._write_face_group(
                default_node,
                K_CCMIO_BOUNDARY_FACES,
                default_map,
                ld,
                model.default_face_ids,
                stream,
                cells,
            )

        for region in regions:
            self._log(
                f"[gph2ccm] writing boundary region '{region.label}' "
                f"({region.face_ids.size} faces) ..."
            )
            region_map = self._add_map(
                root,
                f"boundaryMap-{region.id}",
                (region.face_ids + 1).astype(np.int32),
            )
            region_node = ccmio.new_indexed_entity(
                topology, K_CCMIO_BOUNDARY_FACES, region.id, region.label
            )
            stream = face_stream(ld, region.face_ids)
            cells = boundary_face_cells(ld, region.face_ids)
            self._write_face_group(
                region_node,
                K_CCMIO_BOUNDARY_FACES,
                region_map,
                ld,
                region.face_ids,
                stream,
                cells,
            )

        if self.split_regions:
            self._write_interfaces(root, topology, problem, model, ld)
        elif model.periodic:
            # Periodic pairings are independent of split mode: they join two
            # existing boundary regions, so write them whenever declared.
            self._write_interface_definitions(root, [], model)

        # -- problem description -------------------------------------------
        self._log("[gph2ccm] writing problem description ...")
        # Each cell type becomes one STAR-CCM+ region when split_regions is
        # enabled: the importer keys regions on the CellType "GroupId"
        # (STAR-CCM+ exports write GroupId 1..N and a matching
        # "Region Cell Map <label>" for every cell type).
        for gi, ct in enumerate(model.cell_table, start=1):
            node = ccmio.new_indexed_entity(
                problem, K_CCMIO_CELL_TYPE, ct.id, ct.label
            )
            ccmio.write_optstr(node, "Label", ct.label[:32])
            ccmio.write_optstr(node, "MaterialType", ct.material)
            ccmio.write_opti(node, "GroupId", gi if self.split_regions else 1)
            ccmio.write_opti(node, "MaterialId", 1 if ct.material == "fluid" else 2)

        if model.default_face_ids.size:
            node = ccmio.new_indexed_entity(
                problem, K_CCMIO_BOUNDARY_REGION, 0, "Default_Boundary_Region"
            )
            ccmio.write_optstr(node, "Label", "Default_Boundary_Region")
            ccmio.write_optstr(node, "BoundaryType", "wall")
        for region in regions:
            node = ccmio.new_indexed_entity(
                problem, K_CCMIO_BOUNDARY_REGION, region.id, region.label
            )
            ccmio.write_optstr(node, "Label", region.label[:32])
            ccmio.write_optstr(node, "BoundaryType", region.btype)
            # Optional, user-supplied structured-BC metadata (descriptive
            # only -- gph2ccm never auto-applies a solver condition).  Namespaced
            # under "gph2ccm.BC." so it cannot collide with native CCM keys.
            bc_keys: list[str] = []
            for k, v in region.params.items():
                self.ccmio.write_optstr(node, f"gph2ccm.BC.{k}", str(v))
                bc_keys.append(str(k))
            if bc_keys:
                # The public CCMIO API cannot enumerate child nodes, so the
                # param names are indexed here -- otherwise `gph2ccm inspect`
                # (B1) could never discover them again.
                self.ccmio.write_optstr(node, "gph2ccm.BCKeys", ",".join(bc_keys))

        # -- descriptive field / solver metadata (optional, data-driven) ------
        # Carries the user's field/solver intent from the regions JSON into the
        # .ccm as namespaced descriptive opt nodes.  This is reference metadata
        # only -- gph2ccm never writes actual solution field data or turns
        # itself into a solver-ready exporter (keep-boundary scope decision).
        self._write_metadata_nodes(problem, model)

        # -- capability / limitation notes (informational) --------------------
        self._write_processor_note(problem, model)
        self._write_dimension_note(problem, model)
        self._write_quality_note(problem, model, ld)

        ccmio.write_state(state, problem, "gph2ccm")
        ccmio.write_processor(processor, vertices_node, topology)


def convert_gph(
    gph_path: str | Path,
    out_path: Optional[str | Path] = None,
    *,
    regions_json: Optional[str | Path] = None,
    boundary_types_json: Optional[str | Path] = None,
    ccmio_dll: Optional[str | Path] = None,
    compress: bool = True,
    backup: bool = False,
    title: Optional[str] = None,
    chunk_faces: int = DEFAULT_CHUNK_FACES,
    cell_topology: Optional[str] = None,
    reorder: Optional[str] = None,
    split_regions: bool = False,
    verify: bool = False,
    force_material: Optional[str] = None,
    verbose: bool = True,
) -> Path:
    """Convert a Cradle ``.gph`` mesh to a STAR-CCM+ legacy ``.ccm`` file."""
    gph_path = Path(gph_path).resolve()
    if not gph_path.is_file():
        raise FileNotFoundError(gph_path)
    mesh = parse_gph(gph_path, verbose=verbose)
    if out_path is None:
        out_path = gph_path.with_suffix(".ccm")
    return convert_model(
        mesh,
        out_path,
        regions=load_regions(regions_json),
        boundary_types=load_boundary_types(boundary_types_json),
        ccmio_dll=ccmio_dll,
        compress=compress,
        backup=backup,
        title=title or gph_path.stem,
        chunk_faces=chunk_faces,
        cell_topology=cell_topology,
        reorder=reorder,
        split_regions=split_regions,
        verify=verify,
        force_material=force_material,
        verbose=verbose,
    )


def convert_model(
    mesh: dict,
    out_path: str | Path,
    *,
    regions: Optional[dict] = None,
    boundary_types: Optional[dict[str, str]] = None,
    ccmio_dll: Optional[str | Path] = None,
    compress: bool = True,
    backup: bool = False,
    title: Optional[str] = None,
    chunk_faces: int = DEFAULT_CHUNK_FACES,
    cell_topology: Optional[str] = None,
    reorder: Optional[str] = None,
    split_regions: bool = False,
    verify: bool = False,
    force_material: Optional[str] = None,
    verbose: bool = True,
) -> Path:
    """Convert a parsed GPH ``mesh`` dict to a ``.ccm`` file."""
    if out_path is None:
        raise ValueError("out_path is required")
    out_path = Path(out_path).resolve()

    if boundary_types is None and regions and "boundary_types" in regions:
        boundary_types = {str(k): str(v) for k, v in regions["boundary_types"].items()}

    if reorder:
        mesh = apply_mesh_reorder(mesh, reorder, verbose=verbose)

    model = build_model(
        mesh, regions, boundary_types, force_material,
        split_regions=split_regions,
        boundary_conditions=regions.get("boundary_conditions") if regions else None,
        fields=regions.get("fields") if regions else None,
        solver_settings=regions.get("solver_settings") if regions else None,
        mrf=regions.get("mrf") if regions else None,
        periodic=regions.get("periodic") if regions else None,
    )
    if verbose:
        extra = ""
        n_meta = (
            len(model.fields) + len(model.solver_settings)
            + len(model.mrf) + len(model.periodic)
        )
        if n_meta:
            extra = f", {n_meta} descriptive metadata descriptors"
        print(
            f"[gph2ccm] model: {model.n_cells} cells, "
            f"{model.internal_face_ids.size} internal faces, "
            f"{sum(r.face_ids.size for r in model.boundary_regions)} boundary faces "
            f"in {len(model.boundary_regions)} regions, "
            f"{len(model.cell_table)} cell types{extra}"
        )
        if model.default_face_ids.size:
            print(
                f"[gph2ccm] warning: {model.default_face_ids.size} boundary faces "
                "are not covered by LS_SurfaceRegions -> Default_Boundary_Region"
            )

    if out_path.exists():
        if backup:
            bak = out_path.with_suffix(out_path.suffix + ".bak")
            shutil.move(str(out_path), str(bak))
            if verbose:
                print(f"[gph2ccm] existing output moved to {bak}")
        else:
            out_path.unlink()

    ccmio = CCMIO(ccmio_dll)
    if verbose:
        print(f"[gph2ccm] using CCMIO library: {ccmio.path}")
    writer = CcmMeshWriter(
        ccmio,
        out_path,
        title=title,
        chunk_faces=chunk_faces,
        cell_topology=cell_topology,
        split_regions=split_regions,
        verbose=verbose,
    )
    writer.write(model, mesh["link_data"])

    if compress:
        if verbose:
            print("[gph2ccm] compressing output ...")
        ccmio.compress(out_path)

    if verify:
        from .verify import verify_ccm

        if verbose:
            print("[gph2ccm] verifying output ...")
        verify_ccm(
            out_path, ccmio=ccmio, verbose=verbose, split_regions=split_regions
        )

    if verbose:
        size_mb = out_path.stat().st_size / 1e6
        print(f"[gph2ccm] done: {out_path} ({size_mb:.1f} MB)")
    return out_path


def apply_mesh_reorder(mesh: dict, mode: str, verbose: bool = True) -> dict:
    """Renumber cells (RCM) so the STAR-CCM+ import reorder has less work."""
    if mode != "rcm":
        raise ValueError(f"unsupported reorder mode: {mode}")
    from .reorder import apply_cell_order, rcm_order

    ld = mesh["link_data"]
    owner = ld["owner"]
    neigh = ld["neighbor"]
    n_cells = int(ld["n_cells"])
    boundary_cells = owner[np.asarray(ld["boundary_faces"], dtype=np.int64)]
    t0 = time.perf_counter()
    if verbose:
        print("[gph2ccm] computing RCM cell order ...")
    perm = rcm_order(owner, neigh, n_cells, boundary_cells)
    if verbose:
        print(f"[gph2ccm] RCM order done [{time.perf_counter() - t0:.1f}s]")
    return apply_cell_order(mesh, perm)
