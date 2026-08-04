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
        verbose: bool = True,
    ):
        self.ccmio = ccmio
        self.out_path = Path(out_path)
        self.title = title
        self.chunk_vertices = chunk_vertices
        self.chunk_faces = chunk_faces
        self.cell_topology = cell_topology
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
    ) -> None:
        # NOTE: same ccmio.dll limitation as _write_vertices.  The face
        # vertex stream is 1-D so chunked writes are safe, but the internal
        # face-cells array is [2][n] and chunked writes land at half the
        # intended offset.  Write the whole face-cells array in one call.
        if cells.size:
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

        # -- problem description -------------------------------------------
        self._log("[gph2ccm] writing problem description ...")
        problem = ccmio.new_entity(root, K_CCMIO_PROBLEM_DESCRIPTION, "gph2ccm mesh")
        for ct in model.cell_table:
            node = ccmio.new_indexed_entity(
                problem, K_CCMIO_CELL_TYPE, ct.id, ct.label
            )
            ccmio.write_optstr(node, "Label", ct.label[:32])
            ccmio.write_optstr(node, "MaterialType", ct.material)

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

    model = build_model(mesh, regions, boundary_types, force_material)
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
