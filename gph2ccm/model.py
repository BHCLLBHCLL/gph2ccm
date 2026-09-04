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
    params: dict = field(default_factory=dict)  # optional structured BC metadata
    # (user-supplied, descriptive only -- never auto-applied as a solver setup;
    #  see the "keep boundary" scope decision for gph2ccm).


@dataclass
class SolutionField:
    """An actual solution field written as CCM FieldPhase/Field/FieldData (C2).

    Unlike the *descriptive* ``CcmModel.fields`` entries (``gph2ccm.Field.*``
    metadata nodes), these carry real per-cell float data that STAR-CCM+ can
    import as imported solver post data.  Vector fields are detected from the
    data shape (``(n, 3)``) and written as X/Y/Z component sub-fields using
    the official ``writeexample.cpp`` pattern.
    """

    name: str  # full field name, <= 32 chars (kCCMIOMaxStringLength)
    data: np.ndarray  # (n_cells,) scalar or (n_cells, 3) vector, float
    short_name: str = ""  # prostar short name, <= 8 chars; derived if empty
    location: str = "cell"  # "cell" (vertex fields not yet supported)
    phase: int = 0  # FieldPhase index (0 = default / steady state)
    units: str = ""  # descriptive only; recorded in the gph2ccm metadata layer


# CCM format limits (ccmiotypes.h; kept in sync with ccmio.py without
# importing the ctypes layer from the pure model module).
K_CCMIO_MAX_STRING_LENGTH = 32
K_CCMIO_PROSTAR_SHORT_NAME_LENGTH = 8


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
    # Optional, user-supplied descriptive metadata (data-driven via regions
    # JSON).  Never auto-applied: gph2ccm stays a mesh+description exporter,
    # not a solver-ready exporter (see the "keep boundary" scope decision).
    fields: list = field(default_factory=list)  # e.g. [{"name","location","type","units"}]
    solver_settings: dict = field(default_factory=dict)  # e.g. {"turbulence_model":"k-epsilon"}
    mrf: list = field(default_factory=list)  # rotating reference frames, descriptive
    periodic: list = field(default_factory=list)  # periodic/sliding pairings, descriptive
    # C2: actual solution field data (written as real CCM Field entities).
    solution_fields: list[SolutionField] = field(default_factory=list)
    # E2: solution restart labelling (iteration/time), e.g.
    # {"iteration": 42, "time": 0.25, "time_units": "s", "start_angle": 0.0}.
    # Written as the CCMIO restart node under the solution field set so
    # STAR-CCM+ can display time/iteration for the imported post data.
    restart_info: dict = field(default_factory=dict)

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


# Valid legacy CCM BoundaryType tokens (STAR-CD / STAR-CCM+).  Anything the
# user supplies that is not in this set is treated as a free-form hint and
# mapped to the closest valid token.
_CCM_BOUNDARY_TYPES = {
    "wall", "inlet", "outlet", "pressure", "symmetry", "periodic",
    "fan", "radiator", "porous", "inletvent", "outletvent", "intake",
    "exhaust", "free", "mass", "couple", "blank", "dissolve", "slide",
    "cyclic", "interface",
}

_TYPE_HINTS = {
    "velocity-inlet": "inlet",
    "velocityinlet": "inlet",
    "mass-flow-inlet": "inlet",
    "massflowinlet": "inlet",
    "pressure-inlet": "inlet",
    "pressureinlet": "inlet",
    "pressure-outlet": "outlet",
    "pressureoutlet": "outlet",
    "outflow": "outlet",
    "pressure-far-field": "pressure",
    "wall": "wall",
    "symmetry-plane": "symmetry",
    "symmetryplane": "symmetry",
    "periodic": "periodic",
    "cyclic": "cyclic",
    "interface": "interface",
}


def _normalize_bctype(hint: str, fallback: str = "wall") -> str:
    """Map a user-supplied boundary-type hint to a valid CCM BoundaryType.

    Accepts both the canonical CCM tokens and common Cradle/solver names
    (e.g. ``velocity-inlet`` -> ``inlet``).  Falls back to *fallback* when
    nothing matches, so a bad hint never produces an invalid CCM type.
    """
    h = str(hint).strip().lower()
    if h in _CCM_BOUNDARY_TYPES:
        return h
    if h in _TYPE_HINTS:
        return _TYPE_HINTS[h]
    # Try a substring match against the hint table as a last resort.
    for key, token in _TYPE_HINTS.items():
        if key in h or h in key:
            return token
    return fallback


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
    mesh: dict,
    boundary_types: Optional[dict[str, str]] = None,
    boundary_conditions: Optional[dict] = None,
) -> tuple[list[BoundaryRegion], np.ndarray]:
    """Return ``(regions, default_face_ids)``.

    ``LS_SurfaceRegions`` become CCM boundary regions (id 1..N).  Boundary
    faces not claimed by any surface region go to the implicit region 0
    (``Default_Boundary_Region``).

    ``boundary_conditions`` is an optional, user-supplied mapping
    ``{region_label: {"type": <ccm-type-or-hint>, "params": {<k>: <v>}}}``
    (typically coming from a ``regions`` JSON).  It only *enriches* the
    descriptive metadata of a region -- it never turns gph2ccm into a
    solver-ready exporter.
    """
    ld = mesh.get("link_data") or {}
    n_faces = int(ld.get("n_faces", 0))
    neigh = np.asarray(ld.get("neighbor", np.empty(0, np.int64)), dtype=np.int64)
    boundary_faces = np.asarray(ld.get("boundary_faces", []), dtype=np.int64)

    boundary_types = boundary_types or {}
    # Normalise the optional structured-BC map once.
    bc_map: dict[str, dict] = {}
    for name, spec in (boundary_conditions or {}).items():
        if not isinstance(spec, dict):
            continue
        bc_map[str(name)] = spec

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
        btype = boundary_types.get(name, guess_boundary_type(name))
        # Optional structured-BC override (descriptive only).
        params: dict = {}
        if name in bc_map:
            spec = bc_map[name]
            hint = spec.get("type")
            if hint:
                btype = _normalize_bctype(hint, fallback=btype)
            params = dict(spec.get("params") or {})
        regions.append(
            BoundaryRegion(
                id=len(regions) + 1,
                label=name,
                btype=btype,
                face_ids=f,
                params=params,
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
    boundary_conditions: Optional[dict] = None,
    fields: Optional[list] = None,
    solver_settings: Optional[dict] = None,
    mrf: Optional[list] = None,
    periodic: Optional[list] = None,
    solution_fields: Optional[list] = None,
    restart_info: Optional[dict] = None,
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
    if boundary_conditions is None and regions:
        boundary_conditions = regions.get("boundary_conditions") or None
    # Optional descriptive metadata (data-driven, never auto-applied).
    if fields is None and regions:
        fields = regions.get("fields") or []
    if solver_settings is None and regions:
        solver_settings = regions.get("solver_settings") or {}
    if mrf is None and regions:
        mrf = regions.get("mrf") or []
    if periodic is None and regions:
        periodic = regions.get("periodic") or []
    boundary_regions, default_ids = build_boundary_regions(
        mesh, boundary_types, boundary_conditions
    )

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
        fields=list(fields) if fields else [],
        solver_settings=dict(solver_settings) if solver_settings else {},
        mrf=list(mrf) if mrf else [],
        periodic=list(periodic) if periodic else [],
        solution_fields=_normalize_solution_fields(
            solution_fields, n_cells=int(cell_types.size)
        ),
        restart_info=dict(restart_info) if restart_info else {},
    )


def _normalize_solution_fields(
    raw: Optional[list], n_cells: Optional[int] = None
) -> list[SolutionField]:
    """Coerce ``solution_fields`` entries to validated :class:`SolutionField`.

    Fail-fast validation happens here (before any output exists):
    name length, short-name length, data shape vs cell count, and the
    2-D/3-D vector conventions.
    """
    if not raw:
        return []
    out: list[SolutionField] = []
    for i, item in enumerate(raw):
        if isinstance(item, SolutionField):
            f = item
        elif isinstance(item, dict):
            f = SolutionField(
                name=str(item["name"]),
                data=item["data"],
                short_name=str(item.get("short_name") or ""),
                location=str(item.get("location") or "cell"),
                phase=int(item.get("phase") or 0),
                units=str(item.get("units") or ""),
            )
        else:
            raise TypeError(
                f"solution_fields[{i}]: expected SolutionField or dict, "
                f"got {type(item).__name__}"
            )
        if not f.name:
            raise ValueError(f"solution_fields[{i}]: name is required")
        if len(f.name) > K_CCMIO_MAX_STRING_LENGTH:
            raise ValueError(
                f"solution_fields[{i}] ({f.name!r}): CCM field names are "
                f"limited to {K_CCMIO_MAX_STRING_LENGTH} characters "
                "(version_behavior_table #3)"
            )
        short = f.short_name or f.name
        if len(short) > K_CCMIO_PROSTAR_SHORT_NAME_LENGTH:
            short = short[:K_CCMIO_PROSTAR_SHORT_NAME_LENGTH]
        data = np.asarray(f.data, dtype=np.float64)
        if f.location != "cell":
            raise ValueError(
                f"solution_fields[{i}] ({f.name!r}): only 'cell' location is "
                "supported (legacy CCM post data is cell-centred)"
            )
        if data.ndim == 2 and data.shape[1] == 3:
            pass  # vector -- shape checked against n_cells by the writer
        elif data.ndim != 1:
            raise ValueError(
                f"solution_fields[{i}] ({f.name!r}): data must be (n,) scalar "
                f"or (n, 3) vector, got shape {data.shape}"
            )
        if n_cells is not None and data.shape[0] != n_cells:
            raise ValueError(
                f"solution_fields[{i}] ({f.name!r}): data has "
                f"{data.shape[0]} values but the mesh has {n_cells} cells "
                "(e.g. .fph and mesh from different runs)"
            )
        out.append(
            SolutionField(
                name=f.name,
                data=data,
                short_name=short,
                location=f.location,
                phase=f.phase,
                units=f.units,
            )
        )
    return out


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
