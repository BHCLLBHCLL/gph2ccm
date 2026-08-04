"""Build a CCM-ready mesh model from the GPH parsing result."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np


@dataclass
class CellType:
    id: int
    label: str
    material: str  # "fluid" or "solid"


@dataclass
class BoundaryRegion:
    id: int
    label: str
    btype: str
    face_ids: np.ndarray  # global 0-based GPH face ids


@dataclass
class CcmModel:
    vertices: np.ndarray  # (n, 3) float64, metres
    cell_types: np.ndarray  # (n_cells,) int32, 1-based cell-table ids
    cell_table: list[CellType] = field(default_factory=list)
    internal_face_ids: np.ndarray = field(default_factory=lambda: np.empty(0, np.int64))
    interface_faces: list[tuple[str, str, np.ndarray]] = field(
        default_factory=list
    )  # (region_a, region_b, global gph face ids) with a != b
    boundary_regions: list[BoundaryRegion] = field(default_factory=list)
    default_face_ids: np.ndarray = field(default_factory=lambda: np.empty(0, np.int64))
    n_faces: int = 0

    @property
    def n_cells(self) -> int:
        return int(self.cell_types.size)


_SOLID_KEYWORDS = (
    "solid",
    "case",
    "board",
    "pcb",
    "heat",
    "chassis",
    "housing",
    "cover",
    "bracket",
    "structure",
    "block",
)


def _name_matches(pattern: str, name: str) -> bool:
    """Exact or dotted-suffix match (``FPHPARTS.case1`` matches ``case1``)."""
    if pattern == name:
        return True
    if pattern.endswith("." + name) or name.endswith("." + pattern):
        return True
    return False


def _material_for_part(name: str, regions: Optional[dict]) -> str:
    if regions:
        solid = regions.get("solid_regions") or []
        fluid = regions.get("fluid_regions") or []
        if any(_name_matches(p, name) for p in solid):
            return "solid"
        if any(_name_matches(p, name) for p in fluid):
            return "fluid"
    return "solid" if any(k in name.lower() for k in _SOLID_KEYWORDS) else "fluid"


def guess_boundary_type(name: str) -> str:
    """Guess a legacy STAR/CD ``BoundaryType`` from a Cradle region name."""
    n = name.lower()
    if "periodic" in n or "cyclic" in n:
        return "periodic"
    if "rotate" in n:
        return "periodic"
    if "inlet" in n:
        return "inlet"
    if "outlet" in n:
        return "outlet"
    if n.startswith("open") or "opening" in n or "ambient" in n:
        return "pressure"
    if "sym" in n:
        return "symmetry"
    return "wall"


def _spec_values(spec) -> list[int]:
    if isinstance(spec, (set, frozenset, list, tuple)):
        return [int(v) for v in spec]
    return [int(spec)]


def build_cell_table(
    mesh: dict,
    regions: Optional[dict] = None,
    force_material: Optional[str] = None,
) -> tuple[list[CellType], np.ndarray]:
    """Return ``(cell_table, cell_type_ids)``.

    Each GPH Part becomes one CCM cell type; ``LS_CvolIdOfElements`` maps
    every cell to its part.  Cells whose cvol id cannot be resolved fall
    into an extra ``Unassigned`` type.
    """
    ld = mesh.get("link_data") or {}
    n_cells = int(ld.get("n_cells", 0))
    parts = mesh.get("parts_with_cvol") or []
    cvol = mesh.get("cvol_id")

    table: list[CellType] = []
    cvol_to_id: dict[int, int] = {}
    for idx, (name, spec) in enumerate(parts, start=1):
        material = force_material or _material_for_part(name, regions)
        table.append(CellType(idx, name, material))
        for v in _spec_values(spec):
            cvol_to_id[v] = idx

    ids = np.ones(n_cells, dtype=np.int32)
    if cvol is not None and len(cvol) == n_cells and cvol_to_id and n_cells:
        cvol_arr = np.asarray(cvol, dtype=np.int64)
        mapped = np.fromiter(
            (cvol_to_id.get(int(v), 0) for v in cvol_arr),
            dtype=np.int32,
            count=n_cells,
        )
        if (mapped == 0).any():
            unassigned_id = len(table) + 1
            table.append(
                CellType(unassigned_id, "Unassigned", force_material or "fluid")
            )
            mapped[mapped == 0] = unassigned_id
        ids = mapped
    elif n_cells:
        # No part metadata - one implicit cell type.
        table = [CellType(1, "Cells", force_material or "fluid")]
        ids = np.ones(n_cells, dtype=np.int32)
    return table, ids


def build_boundary_regions(
    mesh: dict, boundary_types: Optional[dict[str, str]] = None
) -> tuple[list[BoundaryRegion], np.ndarray]:
    """Return ``(regions, default_face_ids)``.

    ``LS_SurfaceRegions`` become CCM boundary regions (id 1..N).  Boundary
    faces not claimed by any surface region go to the implicit region 0
    (``Default_Boundary_Region``).
    """
    ld = mesh.get("link_data") or {}
    n_faces = int(ld.get("n_faces", 0))
    neigh = np.asarray(ld.get("neighbor", np.empty(0, np.int64)), dtype=np.int64)
    boundary_faces = np.asarray(ld.get("boundary_faces", []), dtype=np.int64)

    boundary_types = boundary_types or {}

    def _clean(name: str, fids) -> Optional[tuple[str, np.ndarray]]:
        f = np.asarray(fids, dtype=np.int64)
        f = f[(f >= 0) & (f < n_faces)]
        if neigh.size:
            f = f[neigh[f] == -1]
        if f.size == 0:
            return None
        # Preserve first-occurrence order, drop duplicates
        _, first = np.unique(f, return_index=True)
        f = f[np.sort(first)]
        return name, f

    # Cradle exports the same physical boundary faces in several overlapping
    # surface regions (e.g. ``open`` and ``@PartSurface_air_domain``, or the
    # part surfaces of a rotating region and its geometry part).  A CCM file
    # must contain each boundary face exactly once, so we de-duplicate:
    # boundary-condition style regions (not ``@PartSurface_``) get priority,
    # remaining faces are claimed by part-surface regions in file order.
    cleaned = [_clean(n, f) for n, f in (mesh.get("surface_regions") or [])]
    cleaned = [c for c in cleaned if c is not None]
    bc_regions = [c for c in cleaned if not c[0].startswith("@PartSurface_")]
    part_regions = [c for c in cleaned if c[0].startswith("@PartSurface_")]

    used = np.zeros(n_faces, dtype=bool)
    regions: list[BoundaryRegion] = []
    for name, f in bc_regions + part_regions:
        f = f[~used[f]]
        if f.size == 0:
            continue
        regions.append(
            BoundaryRegion(
                id=len(regions) + 1,
                label=name,
                btype=boundary_types.get(name, guess_boundary_type(name)),
                face_ids=f,
            )
        )
        used[f] = True

    if boundary_faces.size:
        default = boundary_faces[~used[boundary_faces]]
    else:
        default = np.empty(0, dtype=np.int64)
    return regions, default


def build_model(
    mesh: dict,
    regions: Optional[dict] = None,
    boundary_types: Optional[dict[str, str]] = None,
    force_material: Optional[str] = None,
    split_regions: bool = False,
) -> CcmModel:
    """Assemble the CCM mesh model from a ``parse_gph_mesh`` result."""
    vertices = np.asarray(mesh["vertices"], dtype=np.float64)
    ld = mesh["link_data"]
    if vertices.ndim != 2 or vertices.shape[1] < 3:
        raise ValueError("GPH vertices must be an (n, 3) array")
    vertices = vertices[:, :3]

    cell_table, cell_types = build_cell_table(mesh, regions, force_material)
    if boundary_types is None and regions:
        boundary_types = regions.get("boundary_types") or None
    boundary_regions, default_ids = build_boundary_regions(mesh, boundary_types)

    neigh = np.asarray(ld["neighbor"], dtype=np.int64)
    owner = np.asarray(ld["owner"], dtype=np.int64)
    internal_ids = np.flatnonzero(neigh >= 0).astype(np.int64)

    interface_faces = []
    if split_regions:
        # Internal faces whose two cells belong to different cell types are
        # the region interfaces.  They must not stay in the CCM internal-face
        # set: STAR-CCM+ would otherwise merge the two fluid regions into
        # one.  Each such face is written later as a pair of boundary faces
        # (one per side) plus an "[Interface N]" surface pair (convert.py).
        label_of = {ct.id: ct.label for ct in cell_table}
        if internal_ids.size and label_of:
            o_ct = cell_types[owner[internal_ids]]
            n_ct = cell_types[neigh[internal_ids]]
            cross = o_ct != n_ct
            iface_ids = internal_ids[cross]
            internal_ids = internal_ids[~cross]
            lo = np.minimum(o_ct[cross], n_ct[cross]).astype(np.int64)
            hi = np.maximum(o_ct[cross], n_ct[cross]).astype(np.int64)
            n_types = max(len(cell_table), 1)
            key = lo * (n_types + 1) + hi
            order = np.argsort(key, kind="stable")
            key = key[order]
            iface_ids = iface_ids[order]
            bounds = np.flatnonzero(np.concatenate(
                [[True], key[1:] != key[:-1], [True]]
            ))
            interface_faces = [
                (
                    label_of[int(lo[order[b]])],
                    label_of[int(hi[order[b]])],
                    iface_ids[b:e],
                )
                for b, e in zip(bounds[:-1], bounds[1:])
            ]

    return CcmModel(
        vertices=vertices,
        cell_types=cell_types,
        cell_table=cell_table,
        internal_face_ids=internal_ids,
        interface_faces=interface_faces,
        boundary_regions=boundary_regions,
        default_face_ids=default_ids,
        n_faces=int(ld["n_faces"]),
    )


# ---------------------------------------------------------------------------
# Face-stream / face-cell builders (vectorised)
# ---------------------------------------------------------------------------


def face_stream(ld: dict, face_ids: np.ndarray) -> np.ndarray:
    """Build the CCM vertex stream for *face_ids* (global GPH face indices).

    Output is ``int32`` with, for each face: ``nVerts v1 v2 ... vn`` using
    **1-based** vertex ids.
    """
    face_ids = np.asarray(face_ids, dtype=np.int64)
    n_faces = int(face_ids.size)
    if n_faces == 0:
        return np.empty(0, dtype=np.int32)

    npe = np.asarray(ld["npe"], dtype=np.int64)[face_ids]
    offsets = np.asarray(ld["face_offsets"], dtype=np.int64)
    src_start = offsets[face_ids]
    total_v = int(npe.sum())

    stream = np.empty(n_faces + total_v, dtype=np.int32)
    starts = np.empty(n_faces, dtype=np.int64)
    starts[0] = 0
    if n_faces > 1:
        np.cumsum(npe[:-1] + 1, out=starts[1:])
    stream[starts] = npe.astype(np.int32)

    if total_v:
        seg = np.repeat(np.arange(n_faces), npe)
        cum = np.cumsum(npe)
        within = np.arange(total_v) - np.repeat(cum - npe, npe)
        dst = np.repeat(starts + 1, npe) + within
        src = np.repeat(src_start, npe) + within
        stream[dst] = np.asarray(ld["face_nodes"], dtype=np.int64)[src] + 1
    return stream


def internal_face_cells(ld: dict, face_ids: np.ndarray) -> np.ndarray:
    """Owner/neighbour pairs (1-based, C order ``[nFaces][2]``)."""
    face_ids = np.asarray(face_ids, dtype=np.int64)
    n = face_ids.size
    out = np.empty(2 * n, dtype=np.int32)
    out[0::2] = np.asarray(ld["owner"], dtype=np.int64)[face_ids] + 1
    out[1::2] = np.asarray(ld["neighbor"], dtype=np.int64)[face_ids] + 1
    return out


def boundary_face_cells(ld: dict, face_ids: np.ndarray) -> np.ndarray:
    """Owner cell ids (1-based) for boundary faces."""
    face_ids = np.asarray(face_ids, dtype=np.int64)
    return (np.asarray(ld["owner"], dtype=np.int64)[face_ids] + 1).astype(np.int32)
