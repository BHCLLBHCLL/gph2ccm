"""GPH -> CCM orchestration."""

from __future__ import annotations

import json
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
from .model import (
    CcmModel,
    build_model,
    boundary_face_cells,
    face_stream,
    internal_face_cells,
)

DEFAULT_CHUNK_VERTICES = 1_000_000
DEFAULT_CHUNK_FACES = 500_000


def load_regions(path: Optional[str | Path]) -> Optional[dict]:
    if path is None:
        return None
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"regions JSON not found: {p}")
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


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


def cell_centroids(ld: dict, n_cells: int, face_centroids: np.ndarray) -> np.ndarray:
    """Cell centroid as the mean of its face centroids."""
    owner = np.asarray(ld["owner"], dtype=np.int64)
    neigh = np.asarray(ld["neighbor"], dtype=np.int64)
    csum = np.zeros((n_cells, 3))
    np.add.at(csum, owner, face_centroids)
    valid = neigh >= 0
    np.add.at(csum, neigh[valid], face_centroids[valid])
    cnt = np.bincount(
        np.concatenate([owner, neigh[valid]]), minlength=n_cells
    )
    return csum / np.maximum(cnt[:, None], 1)


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


class CcmMeshWriter:
    """Write a :class:`CcmModel` to a legacy ``.ccm`` file via CCMIO."""

    def __init__(
        self,
        ccmio: CCMIO,
        out_path: str | Path,
        *,
        title: Optional[str] = None,
        chunk_vertices: int = DEFAULT_CHUNK_VERTICES,
        chunk_faces: int = DEFAULT_CHUNK_FACES,
        cell_topology: Optional[str] = None,
        split_regions: bool = False,
        verbose: bool = True,
    ):
        self.ccmio = ccmio
        self.out_path = Path(out_path)
        self.title = title
        self.chunk_vertices = chunk_vertices
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
        centroids = cell_centroids(ld, model.n_cells, fc)
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

        self._write_interface_definitions(root, records)

    def _write_interface_definitions(self, root, records) -> None:
        """Write the STAR-CCM+ ``InterfaceDefinitions`` node (root child).

        Mirrors the structure written by STAR-CCM+ itself (see
        ``bladerotating_dm2.ccm``): one ``Interface-N`` node per interface
        with ``Name``, ``Boundary0``/``Boundary1`` (boundary-region ids of
        the two per-side patches), ``Configuration`` and ``ConditionType``.
        """
        if not records:
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

    def write(self, model: CcmModel, ld: dict) -> None:
        ccmio = self.ccmio
        out = self.out_path

        if out.exists():
            if self.verbose:
                print(f"[gph2ccm] removing existing output: {out}")
            out.unlink()

        t0 = time.perf_counter()
        root = ccmio.open_file(out)
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

        ccmio.write_state(state, problem, "gph2ccm")
        ccmio.write_processor(processor, vertices_node, topology)
        ccmio.close_file(root)

        self._log(
            f"[gph2ccm] wrote {out} "
            f"[{time.perf_counter() - t0:.1f}s]"
        )


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
    chunk_vertices: int = DEFAULT_CHUNK_VERTICES,
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
        chunk_vertices=chunk_vertices,
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
    chunk_vertices: int = DEFAULT_CHUNK_VERTICES,
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
    )
    if verbose:
        print(
            f"[gph2ccm] model: {model.n_cells} cells, "
            f"{model.internal_face_ids.size} internal faces, "
            f"{sum(r.face_ids.size for r in model.boundary_regions)} boundary faces "
            f"in {len(model.boundary_regions)} regions, "
            f"{len(model.cell_table)} cell types"
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
        chunk_vertices=chunk_vertices,
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
        verify_ccm(out_path, ccmio=ccmio, verbose=verbose)

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
