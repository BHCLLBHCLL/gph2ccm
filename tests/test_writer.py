"""End-to-end CCM writer test using a synthetic 8-cell hexa mesh.

The mesh is the same box used by libccmio's ``docs/examples/writeexample.cpp``:
27 vertices, 12 internal faces, 24 boundary faces.  We build a GPH-shaped
``link_data`` dict, assemble a :class:`CcmModel`, write a ``.ccm`` through the
CCMIO bindings and read everything back for verification.

Run directly:  ``python tests/test_writer.py``
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gph2ccm.ccmio import (  # noqa: E402
    CCMIO,
    K_CCMIO_BOUNDARY_FACES,
    K_CCMIO_BOUNDARY_REGION,
    K_CCMIO_CELLS,
    K_CCMIO_CELL_TYPE,
    K_CCMIO_INTERNAL_FACES,
    K_CCMIO_PROCESSOR,
    K_CCMIO_TOPOLOGY,
)
from gph2ccm.convert import CcmMeshWriter  # noqa: E402
from gph2ccm.model import build_model  # noqa: E402
from gph2ccm.verify import verify_ccm  # noqa: E402


# -- synthetic GPH data (ported from writeexample.cpp) -----------------------

VERTICES = [
    [0, 0, 0], [0, 0.5, 0], [0, 1, 0], [0.5, 0, 0], [0.5, 0.5, 0],
    [0.5, 1, 0], [1, 0, 0], [1, 0.5, 0], [1, 1, 0],
    [0, 0, 0.5], [0, 0.5, 0.5], [0, 1, 0.5], [0.5, 0, 0.5],
    [0.5, 0.5, 0.5], [0.5, 1, 0.5], [1, 0, 0.5], [1, 0.5, 0.5],
    [1, 1, 0.5],
    [0, 0, 1], [0, 0.5, 1], [0, 1, 1], [0.5, 0, 1], [0.5, 0.5, 1],
    [0.5, 1, 1], [1, 0, 1], [1, 0.5, 1], [1, 1, 1],
]

# (nverts, vertices[1-based], cells[1-based])
INTERNAL = [
    (4, (2, 5, 14, 11), (2, 1)),
    (4, (4, 5, 14, 13), (1, 3)),
    (4, (5, 6, 15, 14), (2, 4)),
    (4, (5, 8, 17, 14), (4, 3)),
    (4, (10, 11, 14, 13), (5, 1)),
    (4, (11, 12, 15, 14), (6, 2)),
    (4, (11, 14, 23, 20), (6, 5)),
    (4, (13, 14, 17, 16), (7, 3)),
    (4, (13, 14, 23, 22), (5, 7)),
    (4, (14, 15, 18, 17), (8, 4)),
    (4, (14, 15, 24, 23), (6, 8)),
    (4, (14, 17, 26, 23), (8, 7)),
]

BOUNDARY = [
    (4, (1, 2, 5, 4), (1, 0)), (4, (1, 10, 11, 2), (1, 0)),
    (4, (1, 4, 13, 10), (1, 0)), (4, (2, 3, 6, 5), (2, 0)),
    (4, (2, 11, 12, 3), (2, 0)), (4, (3, 12, 15, 6), (2, 0)),
    (4, (4, 5, 8, 7), (3, 0)), (4, (4, 7, 16, 13), (3, 0)),
    (4, (5, 6, 9, 8), (4, 0)), (4, (6, 15, 18, 9), (4, 0)),
    (4, (7, 8, 17, 16), (3, 0)), (4, (8, 9, 18, 17), (4, 0)),
    (4, (10, 19, 20, 11), (5, 0)), (4, (10, 13, 22, 19), (5, 0)),
    (4, (11, 20, 21, 12), (6, 0)), (4, (12, 21, 24, 15), (6, 0)),
    (4, (13, 16, 25, 22), (7, 0)), (4, (15, 24, 27, 18), (8, 0)),
    (4, (16, 17, 26, 25), (7, 0)), (4, (17, 18, 27, 26), (8, 0)),
    (4, (19, 22, 23, 20), (5, 0)), (4, (20, 23, 24, 21), (6, 0)),
    (4, (22, 25, 26, 23), (7, 0)), (4, (23, 26, 27, 24), (8, 0)),
]


def make_synthetic_gph() -> dict:
    faces = INTERNAL + BOUNDARY
    n_faces = len(faces)
    npe = np.full(n_faces, 4, dtype=np.int64)
    conn = []
    owner = []
    neigh = []
    for nv, verts, cells in faces:
        conn.extend(v - 1 for v in verts)
        owner.append(cells[0] - 1)
        neigh.append(cells[1] - 1 if cells[1] else -1)
    face_offsets = np.zeros(n_faces + 1, dtype=np.int64)
    np.cumsum(npe, out=face_offsets[1:])
    return {
        "vertices": np.asarray(VERTICES, dtype=np.float64),
        "n_vertices": len(VERTICES),
        "link_data": {
            "n_faces": n_faces,
            "n_cells": 8,
            "npe": npe,
            "face_nodes": np.asarray(conn, dtype=np.int64),
            "face_offsets": face_offsets,
            "owner": np.asarray(owner, dtype=np.int64),
            "neighbor": np.asarray(neigh, dtype=np.int64),
            "boundary_faces": list(range(12, n_faces)),
        },
        "cvol_id": np.ones(8, dtype=np.int64),
        "parts_with_cvol": [("fluid", 1)],
        "volume_regions": ["FluidRegion"],
        "surface_regions": [
            ("inlet", np.arange(12, 18, dtype=np.int64)),
            ("outlet", np.arange(18, 24, dtype=np.int64)),
        ],
    }


def read_mesh(ccmio: CCMIO, path: Path):
    """Open *path* read-only and return a dict of everything written."""
    import ctypes

    err = ctypes.c_int(0)
    root = ccmio.open_file_readonly(str(path))
    state, problem = ccmio.get_state(root)
    processor = ccmio.next_entity(state, K_CCMIO_PROCESSOR, 0)
    assert processor is not None
    vertices, topology, _, _ = ccmio.read_processor(processor)

    dims, scale, vertex_map, coords = ccmio.read_vertices(vertices)
    cell_entity = ccmio.get_entity(topology, K_CCMIO_CELLS, 0)
    cell_map, cell_types = ccmio.read_cells(cell_entity)
    cell_topology = ccmio.read_opt1i(cell_entity, "CellTopologyType", cell_types.size)

    internal = ccmio.get_entity(topology, K_CCMIO_INTERNAL_FACES, 0)
    internal_map, internal_stream = ccmio.read_faces(internal, K_CCMIO_INTERNAL_FACES)
    internal_cells = ccmio.read_face_cells(internal, K_CCMIO_INTERNAL_FACES)

    boundary = {}
    for region_id in (0, 1, 2):
        entity = ccmio.get_entity(topology, K_CCMIO_BOUNDARY_FACES, region_id)
        bmap, bstream = ccmio.read_faces(entity, K_CCMIO_BOUNDARY_FACES)
        bcells = ccmio.read_face_cells(entity, K_CCMIO_BOUNDARY_FACES)
        boundary[region_id] = (ccmio.read_map(bmap, bstream.size // 5), bstream, bcells)

    cell_types_info = {}
    for cid in (1,):
        node = ccmio.get_entity(problem, K_CCMIO_CELL_TYPE, cid)
        cell_types_info[cid] = (
            ccmio.read_optstr(node, "Label"),
            ccmio.read_optstr(node, "MaterialType"),
        )
    regions_info = {}
    for rid in (0, 1, 2):
        node = ccmio.get_entity(problem, K_CCMIO_BOUNDARY_REGION, rid)
        regions_info[rid] = (
            ccmio.read_optstr(node, "Label"),
            ccmio.read_optstr(node, "BoundaryType"),
        )

    info = {
        "dims": dims,
        "scale": scale,
        "coords": coords,
        "vertex_map": ccmio.read_map(vertex_map, coords.shape[0]),
        "cell_map": ccmio.read_map(cell_map, cell_types.size),
        "cell_types": cell_types,
        "cell_topology": cell_topology,
        "internal_map": ccmio.read_map(internal_map, internal_stream.size // 5),
        "internal_stream": internal_stream,
        "internal_cells": internal_cells,
        "boundary": boundary,
        "cell_types_info": cell_types_info,
        "regions_info": regions_info,
    }
    ccmio.close_file(root)
    return info


def test_write_and_readback() -> None:
    mesh = make_synthetic_gph()
    model = build_model(mesh, {"fluid_regions": ["fluid"]})

    assert model.n_cells == 8
    assert model.vertices.shape == (27, 3)
    assert model.internal_face_ids.size == 12
    assert len(model.boundary_regions) == 2
    assert model.boundary_regions[0].label == "inlet"
    assert model.boundary_regions[0].face_ids.size == 6
    assert model.boundary_regions[1].btype == "outlet"
    assert model.default_face_ids.size == 12
    assert model.cell_table[0].material == "fluid"

    ccmio = CCMIO()
    with tempfile.TemporaryDirectory(prefix="gph2ccm_test_") as tmp:
        out = Path(tmp) / "box.ccm"
        writer = CcmMeshWriter(ccmio, out, verbose=False)
        writer = CcmMeshWriter(ccmio, out, verbose=False, cell_topology="poly")
        writer.write(model, mesh["link_data"])
        ccmio.compress(out)
        assert out.stat().st_size > 0

        info = read_mesh(ccmio, out)
        summary = verify_ccm(out, ccmio=ccmio, verbose=False)
        assert summary["n_vertices"] == 27
        assert summary["n_cells"] == 8
        assert summary["n_internal_faces"] == 12
        assert summary["n_boundary_faces"] == 24
        assert summary["boundary_regions"][1]["label"] == "inlet"
        assert summary["boundary_regions"][2]["type"] == "outlet"

    assert info["dims"] == 3
    assert abs(info["scale"] - 0.001) < 1e-9
    np.testing.assert_allclose(info["coords"], np.asarray(VERTICES) * 1000.0, atol=1e-3)
    np.testing.assert_array_equal(info["vertex_map"], np.arange(1, 28, dtype=np.int32))
    np.testing.assert_array_equal(info["cell_map"], np.arange(1, 9, dtype=np.int32))
    np.testing.assert_array_equal(info["cell_types"], np.ones(8, dtype=np.int32))
    # CellTopologyType must be present and polyhedral
    np.testing.assert_array_equal(
        info["cell_topology"], np.full(8, 255, dtype=np.int32)
    )

    # internal faces: stream = 12 quads, 5 ints each
    assert info["internal_stream"].size == 60
    expected_stream = []
    for nv, verts, _ in INTERNAL:
        expected_stream.append(nv)
        expected_stream.extend(verts)
    np.testing.assert_array_equal(info["internal_stream"], expected_stream)
    np.testing.assert_array_equal(info["internal_map"], np.arange(1, 13, dtype=np.int32))
    expected_cells = []
    for _, _, cells in INTERNAL:
        expected_cells.extend(cells)
    np.testing.assert_array_equal(info["internal_cells"].ravel(), expected_cells)

    # boundary region 1 = "inlet" -> global faces 13..18
    bmap, bstream, bcells = info["boundary"][1]
    np.testing.assert_array_equal(bmap, np.arange(13, 19, dtype=np.int32))
    assert bstream.size == 30
    expected_b = []
    for nv, verts, _ in BOUNDARY[:6]:
        expected_b.append(nv)
        expected_b.extend(verts)
    np.testing.assert_array_equal(bstream, expected_b)
    np.testing.assert_array_equal(
        bcells.ravel(), np.array([1, 1, 1, 2, 2, 2], dtype=np.int32)
    )

    assert info["cell_types_info"] == {1: ("fluid", "fluid")}
    assert info["regions_info"][0] == ("Default_Boundary_Region", "wall")
    assert info["regions_info"][1] == ("inlet", "inlet")
    assert info["regions_info"][2] == ("outlet", "outlet")
    print("test_write_and_readback OK")


def test_model_build_parts_and_boundaries() -> None:
    mesh = make_synthetic_gph()
    ld = mesh["link_data"]
    n_faces = ld["n_faces"]
    # Two parts: cells 0..3 -> solid "case", 4..7 -> fluid "air"
    mesh["cvol_id"] = np.array([10, 10, 10, 10, 20, 20, 20, 20], dtype=np.int64)
    mesh["parts_with_cvol"] = [("case", 10), ("air", 20)]
    # An unknown cvol -> Unassigned fallback
    mesh["cvol_id"][0] = 99
    # Boundary regions incl. empty + heuristic types
    mesh["surface_regions"] = [
        ("inlet_1", np.arange(12, 18, dtype=np.int64)),
        ("open_side", np.arange(18, 24, dtype=np.int64)),
        ("empty_region", np.array([3], dtype=np.int64)),  # internal face -> dropped
        ("@PartSurface_dup", np.arange(12, 18, dtype=np.int64)),  # dup of inlet_1
        ("@PartSurface_extra", np.arange(24, 30, dtype=np.int64)),  # default faces
    ]
    # one custom override
    model = build_model(
        mesh,
        {
            "fluid_regions": ["FPHPARTS.air"],
            "solid_regions": ["case"],
            "boundary_types": {"inlet_1": "massflowinlet"},
        },
    )

    assert [ct.label for ct in model.cell_table] == ["case", "air", "Unassigned"]
    assert model.cell_table[0].material == "solid"
    assert model.cell_table[1].material == "fluid"
    assert model.cell_types.tolist() == [3, 1, 1, 1, 2, 2, 2, 2]

    assert [r.label for r in model.boundary_regions] == [
        "inlet_1",
        "open_side",
        "@PartSurface_extra",
    ]
    assert model.boundary_regions[0].btype == "massflowinlet"
    assert model.boundary_regions[1].btype == "pressure"
    assert model.boundary_regions[2].face_ids.size == 6
    # 24 boundary faces - 18 claimed by regions = 6 default
    assert model.default_face_ids.size == 6
    print("test_model_build_parts_and_boundaries OK")


# -- two-part (rotor/stator) mesh for split_regions / interface tests -------
# Same 8-cell hex box, but cells 0..3 belong to part "rotor" and 4..7 to
# "stator".  Several internal faces straddle the two parts, which (in
# split_regions mode) become grid-interface faces.

def make_split_gph() -> dict:
    mesh = make_synthetic_gph()
    mesh["cvol_id"] = np.array([1, 1, 1, 1, 2, 2, 2, 2], dtype=np.int64)
    mesh["parts_with_cvol"] = [("rotor", 1), ("stator", 2)]
    return mesh


SPLIT_REGIONS = {"fluid_regions": ["rotor", "stator"]}


def test_split_regions_builds_interface_faces() -> None:
    """split_regions must lift cross-part internal faces into interface_faces."""
    mesh = make_split_gph()

    model_split = build_model(mesh, SPLIT_REGIONS, split_regions=True)
    assert model_split.interface_faces, "expected at least one region interface"
    # Every interface record is a pair of distinct cell-type labels.
    for label_a, label_b, fids in model_split.interface_faces:
        assert label_a != label_b
        assert fids.size > 0
    # Those faces must NOT also live in the plain internal-face set.
    iface_ids = np.concatenate(
        [f for _, _, f in model_split.interface_faces]
    ) if model_split.interface_faces else np.empty(0, np.int64)
    assert not np.isin(iface_ids, model_split.internal_face_ids).any()

    # Without split_regions the same mesh has no interfaces.
    model_plain = build_model(mesh, SPLIT_REGIONS)
    assert not model_plain.interface_faces
    assert model_plain.internal_face_ids.size > model_split.internal_face_ids.size
    print("test_split_regions_builds_interface_faces OK")


def _read_interface_definitions(ccmio: "CCMIO", path: "Path") -> list[dict]:
    """Return the list of ``Interface-N`` sub-records under InterfaceDefinitions."""
    import ctypes

    from gph2ccm.ccmio import CCMIONode

    lib = ccmio._lib
    lib.CCMIOGetNode.restype = ctypes.c_int
    lib.CCMIOGetNode.argtypes = [
        ctypes.POINTER(ctypes.c_int),
        CCMIONode,
        ctypes.c_char_p,
        ctypes.POINTER(CCMIONode),
    ]
    lib.CCMIOReadNodestr.restype = ctypes.c_int
    lib.CCMIOReadNodestr.argtypes = [
        ctypes.POINTER(ctypes.c_int),
        CCMIONode,
        ctypes.c_char_p,
        ctypes.POINTER(ctypes.c_char_p),
    ]
    lib.CCMIOReadNodei.restype = ctypes.c_int
    lib.CCMIOReadNodei.argtypes = [
        ctypes.POINTER(ctypes.c_int),
        CCMIONode,
        ctypes.c_char_p,
        ctypes.POINTER(ctypes.c_int),
    ]

    def _get(parent, name):
        err = ctypes.c_int(0)
        node = CCMIONode()
        code = lib.CCMIOGetNode(
            ctypes.byref(err), parent, name.encode(), ctypes.byref(node)
        )
        return code, node

    def _str(node, name):
        # In this libccmio build CCMIOReadNodestr returns the string through a
        # char** (it allocates the buffer), so pass a pointer-to-char-pointer.
        err = ctypes.c_int(0)
        val = ctypes.c_char_p()
        lib.CCMIOReadNodestr(
            ctypes.byref(err), node, name.encode(), ctypes.byref(val)
        )
        return (val.value or b"").decode(errors="replace")

    def _int(node, name):
        err = ctypes.c_int(0)
        val = ctypes.c_int(0)
        lib.CCMIOReadNodei(ctypes.byref(err), node, name.encode(), ctypes.byref(val))
        return val.value

    root = ccmio.open_file_readonly(str(path))
    try:
        code, idf = _get(root.root, "InterfaceDefinitions")
        if code != 0:
            return []
        records = []
        for k in range(64):
            code, inode = _get(idf, f"Interface-{k}")
            if code != 0:
                break
            records.append(
                {
                    "name": _str(inode, "Name"),
                    "boundary0": _int(inode, "Boundary0"),
                    "boundary1": _int(inode, "Boundary1"),
                    "configuration": _str(inode, "Configuration"),
                    "condition_type": _str(inode, "ConditionType"),
                }
            )
        return records
    finally:
        ccmio.close_file(root)


def test_interface_definitions_written() -> None:
    """split mode must emit an InterfaceDefinitions node with paired sides."""
    mesh = make_split_gph()
    model = build_model(mesh, SPLIT_REGIONS, split_regions=True)

    ccmio = CCMIO()
    with tempfile.TemporaryDirectory(prefix="gph2ccm_split_") as tmp:
        out = Path(tmp) / "split.ccm"
        writer = CcmMeshWriter(ccmio, out, verbose=False, split_regions=True)
        writer.write(model, mesh["link_data"])
        ccmio.compress(out)

        records = _read_interface_definitions(ccmio, out)
        assert records, "InterfaceDefinitions node missing in split-mode file"
        rec = records[0]
        # Boundary0/Boundary1 are boundary-region ids of the two per-side
        # interface patches; both must be present and distinct.
        assert rec["boundary0"] >= 0 and rec["boundary1"] >= 0
        assert rec["boundary0"] != rec["boundary1"]
        assert rec["configuration"] == "IN_PLACE"
        assert rec["condition_type"] == "InternalInterface"
    print("test_interface_definitions_written OK")


def test_split_regions_write_and_readback() -> None:
    """End-to-end split write must verify cleanly (H1 regression)."""
    mesh = make_split_gph()
    model = build_model(mesh, SPLIT_REGIONS, split_regions=True)

    ccmio = CCMIO()
    with tempfile.TemporaryDirectory(prefix="gph2ccm_split_") as tmp:
        out = Path(tmp) / "split.ccm"
        writer = CcmMeshWriter(ccmio, out, verbose=False, split_regions=True)
        writer.write(model, mesh["link_data"])
        ccmio.compress(out)

        summary = verify_ccm(out, ccmio=ccmio, verbose=False, split_regions=True)
        assert summary["n_vertices"] == 27
        assert summary["n_cells"] == 8

        # Split mode adds per-side + [Interface N] boundary patches, so the
        # region count must exceed the two plain surface regions.
        labels = [b["label"] for b in summary["boundary_regions"].values()]
        assert len(labels) > 2, f"expected interface patches, got {labels}"
        # Exactly one internal-interface definition must exist.
        records = _read_interface_definitions(ccmio, out)
        assert len(records) == 1

        # Non-split file for the same mesh has no interface patches, so it
        # must have strictly fewer boundary regions than the split file.
        out2 = Path(tmp) / "plain.ccm"
        writer2 = CcmMeshWriter(ccmio, out2, verbose=False)
        writer2.write(
            build_model(mesh, SPLIT_REGIONS), mesh["link_data"]
        )
        ccmio.compress(out2)
        summary2 = verify_ccm(out2, ccmio=ccmio, verbose=False)
        assert len(summary["boundary_regions"]) > len(
            summary2["boundary_regions"]
        ), (
            f"split mode should add interface patches: "
            f"{len(summary['boundary_regions'])} vs "
            f"{len(summary2['boundary_regions'])}"
        )
    print("test_split_regions_write_and_readback OK")


def test_verify_split_no_false_positive() -> None:
    """verify_ccm must not flag duplicated interface face ids as an error."""
    mesh = make_split_gph()
    model = build_model(mesh, SPLIT_REGIONS, split_regions=True)
    ccmio = CCMIO()
    with tempfile.TemporaryDirectory(prefix="gph2ccm_split_") as tmp:
        out = Path(tmp) / "split.ccm"
        writer = CcmMeshWriter(ccmio, out, verbose=False, split_regions=True)
        writer.write(model, mesh["link_data"])
        ccmio.compress(out)
        # In split mode the per-side patches intentionally share face ids,
        # so the relaxed (split_regions=True) check must pass.
        verify_ccm(out, ccmio=ccmio, verbose=False, split_regions=True)
        # The strict check (default) is expected to raise in split mode.
        raised = False
        try:
            verify_ccm(out, ccmio=ccmio, verbose=False)
        except AssertionError:
            raised = True
        assert raised, "strict verify should flag split-mode shared face ids"
    print("test_verify_split_no_false_positive OK")


def test_structured_boundary_conditions() -> None:
    """User-supplied BC metadata (type + params) is carried and written."""
    mesh = make_synthetic_gph()
    bc = {
        "inlet": {
            "type": "velocity-inlet",  # solver hint -> normalised to "inlet"
            "params": {"velocity": [1.0, 0.0, 0.0], "temperature": 300},
        },
        "outlet": {"type": "pressure-outlet", "params": {"pressure": 0.0}},
    }
    model = build_model(mesh, {"fluid_regions": ["fluid"]}, boundary_conditions=bc)

    inlet = next(r for r in model.boundary_regions if r.label == "inlet")
    outlet = next(r for r in model.boundary_regions if r.label == "outlet")
    # Solver hint normalised to a valid CCM BoundaryType token.
    assert inlet.btype == "inlet"
    assert outlet.btype == "outlet"
    # Structured params preserved on the model.
    assert inlet.params == {"velocity": [1.0, 0.0, 0.0], "temperature": 300}

    # Written to the CCM as descriptive, namespaced opt nodes.
    ccmio = CCMIO()
    with tempfile.TemporaryDirectory(prefix="gph2ccm_bc_") as tmp:
        out = Path(tmp) / "bc.ccm"
        writer = CcmMeshWriter(ccmio, out, verbose=False)
        writer.write(model, mesh["link_data"])
        ccmio.compress(out)

        root = ccmio.open_file_readonly(str(out))
        try:
            state, problem = ccmio.get_state(root)
            # Find the boundary-region node whose Label == "inlet".
            node = None
            for e in ccmio.iter_entities(problem, K_CCMIO_BOUNDARY_REGION):
                if ccmio.read_optstr(e, "Label") == "inlet":
                    node = e
                    break
            assert node is not None
            # Descriptive params are stored verbatim under gph2ccm.BC.*
            assert ccmio.read_optstr(node, "gph2ccm.BC.velocity") == "[1.0, 0.0, 0.0]"
            assert ccmio.read_optstr(node, "gph2ccm.BC.temperature") == "300"
        finally:
            ccmio.close_file(root)
    print("test_structured_boundary_conditions OK")


def test_fields_and_solver_metadata() -> None:
    """Optional, data-driven field & solver metadata is carried and written."""
    mesh = make_synthetic_gph()
    regions = {
        "fluid_regions": ["fluid"],
        "fields": [
            {"name": "pressure", "location": "cell", "type": "scalar", "units": "Pa"},
            {"name": "velocity", "location": "cell", "type": "vector", "units": "m/s"},
        ],
        "solver_settings": {
            "turbulence_model": "k-epsilon",
            "steady": True,
        },
    }
    model = build_model(mesh, regions)

    assert len(model.fields) == 2
    assert model.solver_settings.get("turbulence_model") == "k-epsilon"
    assert model.solver_settings.get("steady") is True

    ccmio = CCMIO()
    with tempfile.TemporaryDirectory(prefix="gph2ccm_fields_") as tmp:
        out = Path(tmp) / "fields.ccm"
        writer = CcmMeshWriter(ccmio, out, verbose=False)
        writer.write(model, mesh["link_data"])
        ccmio.compress(out)

        # verify_ccm ignores unknown opt nodes, so the file must still pass.
        verify_ccm(out, ccmio=ccmio, verbose=False)

        root = ccmio.open_file_readonly(str(out))
        try:
            state, problem = ccmio.get_state(root)
            # Each field is encoded as "<location>|<type>|<units>".
            assert ccmio.read_optstr(problem, "gph2ccm.Field.pressure") == "cell|scalar|Pa"
            assert ccmio.read_optstr(problem, "gph2ccm.Field.velocity") == "cell|vector|m/s"
            assert ccmio.read_optstr(problem, "gph2ccm.FieldNames") == "pressure,velocity"
            # Solver settings carried verbatim.
            assert ccmio.read_optstr(problem, "gph2ccm.Solver.turbulence_model") == "k-epsilon"
            assert ccmio.read_optstr(problem, "gph2ccm.Solver.steady") == "True"
            assert (
                ccmio.read_optstr(problem, "gph2ccm.SolverKeys")
                == "turbulence_model,steady"
            )
        finally:
            ccmio.close_file(root)
    print("test_fields_and_solver_metadata OK")


def test_mrf_metadata() -> None:
    """Optional, data-driven MRF (rotating reference frame) metadata."""
    mesh = make_synthetic_gph()
    regions = {
        "fluid_regions": ["fluid"],
        "mrf": [
            {
                "name": "rotor_frame",
                "region": "fluid",
                "type": "rotating",
                "axis": [0, 0, 1],
                "origin": [0, 0, 0],
                "omega": 100.0,
                "units": "rad/s",
            },
        ],
    }
    model = build_model(mesh, regions)
    assert len(model.mrf) == 1

    ccmio = CCMIO()
    with tempfile.TemporaryDirectory(prefix="gph2ccm_mrf_") as tmp:
        out = Path(tmp) / "mrf.ccm"
        writer = CcmMeshWriter(ccmio, out, verbose=False)
        writer.write(model, mesh["link_data"])
        ccmio.compress(out)
        verify_ccm(out, ccmio=ccmio, verbose=False)

        root = ccmio.open_file_readonly(str(out))
        try:
            state, problem = ccmio.get_state(root)
            assert ccmio.read_optstr(problem, "gph2ccm.MRF.rotor_frame") == (
                "fluid|rotating|[0, 0, 1]|[0, 0, 0]|100.0|rad/s"
            )
            assert ccmio.read_optstr(problem, "gph2ccm.MRFNames") == "rotor_frame"
        finally:
            ccmio.close_file(root)
    print("test_mrf_metadata OK")


def test_periodic_pairing_metadata() -> None:
    """Optional, data-driven periodic/sliding interface pairing metadata."""
    mesh = make_synthetic_gph()
    regions = {
        "fluid_regions": ["fluid"],
        "periodic": [
            {
                "name": "per_rot",
                "region": "rotor_side",
                "shadow": "stator_side",
                "type": "rotational",
                "axis": [0, 0, 1],
                "angle": 15.0,
            },
        ],
    }
    model = build_model(mesh, regions)
    assert len(model.periodic) == 1

    ccmio = CCMIO()
    with tempfile.TemporaryDirectory(prefix="gph2ccm_per_") as tmp:
        out = Path(tmp) / "periodic.ccm"
        writer = CcmMeshWriter(ccmio, out, verbose=False)
        writer.write(model, mesh["link_data"])
        ccmio.compress(out)
        verify_ccm(out, ccmio=ccmio, verbose=False)

        root = ccmio.open_file_readonly(str(out))
        try:
            state, problem = ccmio.get_state(root)
            assert ccmio.read_optstr(problem, "gph2ccm.Periodic.per_rot") == (
                "rotor_side|stator_side|rotational|[0, 0, 1]|15.0"
            )
            assert ccmio.read_optstr(problem, "gph2ccm.PeriodicNames") == "per_rot"
        finally:
            ccmio.close_file(root)
    print("test_periodic_pairing_metadata OK")


if __name__ == "__main__":
    test_write_and_readback()
    test_model_build_parts_and_boundaries()
    test_split_regions_builds_interface_faces()
    test_interface_definitions_written()
    test_split_regions_write_and_readback()
    test_verify_split_no_false_positive()
    test_structured_boundary_conditions()
    test_fields_and_solver_metadata()
    test_mrf_metadata()
    test_periodic_pairing_metadata()
