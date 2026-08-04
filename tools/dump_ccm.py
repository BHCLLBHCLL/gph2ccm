"""Dump the CCM entity tree and mesh-topology statistics of a ``.ccm`` file.

Diagnostic companion to ``gph2ccm``: prints the same kind of information for
a hand-written file and for a STAR-CCM+ exported file so the two can be
compared (used to find why STAR-CCM+ hangs in the import reorder step).

Usage:  python tools/dump_ccm.py <file.ccm> [--full]
"""

from __future__ import annotations

import ctypes
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gph2ccm.ccmio import (  # noqa: E402
    CCMIO,
    CCMIOError,
    CCMIOID,
    CCMIONode,
    K_CCMIO_BOUNDARY_FACES,
    K_CCMIO_BOUNDARY_REGION,
    K_CCMIO_CELL_TYPE,
    K_CCMIO_CELLS,
    K_CCMIO_INTERNAL_FACES,
    K_CCMIO_MAP,
    K_CCMIO_PROBLEM_DESCRIPTION,
    K_CCMIO_PROCESSOR,
    K_CCMIO_STATE,
    K_CCMIO_TOPOLOGY,
    K_CCMIO_VERTICES,
    K_CCMIO_NO_NODE_ERR,
)

ENTITY_NAMES = {
    K_CCMIO_MAP: "MAP",
    K_CCMIO_VERTICES: "VERTICES",
    K_CCMIO_TOPOLOGY: "TOPOLOGY",
    K_CCMIO_INTERNAL_FACES: "INTERNAL_FACES",
    K_CCMIO_BOUNDARY_FACES: "BOUNDARY_FACES",
    K_CCMIO_CELLS: "CELLS",
    K_CCMIO_PROBLEM_DESCRIPTION: "PROBLEM_DESCRIPTION",
    K_CCMIO_STATE: "STATE",
    K_CCMIO_PROCESSOR: "PROCESSOR",
    K_CCMIO_CELL_TYPE: "CELL_TYPE",
    K_CCMIO_BOUNDARY_REGION: "BOUNDARY_REGION",
}


def _bind_child_iter(ccmio: CCMIO):
    """Bind CCMIOGetNextChild / CCMIOGetNumberOfChildren / CCMIOEntityName."""
    lib = ccmio._lib
    err_p = ctypes.POINTER(ctypes.c_int)
    id_p = ctypes.POINTER(CCMIOID)

    get_n = getattr(lib, "CCMIOGetNumberOfChildren")
    get_n.restype = ctypes.c_int
    get_n.argtypes = [err_p, CCMIONode, ctypes.POINTER(ctypes.c_int)]

    next_c = getattr(lib, "CCMIOGetNextChild")
    next_c.restype = ctypes.c_int
    next_c.argtypes = [err_p, CCMIONode, ctypes.POINTER(ctypes.c_int), id_p]

    name = getattr(lib, "CCMIOEntityName")
    name.restype = ctypes.c_int
    name.argtypes = [err_p, CCMIOID, ctypes.c_char_p]

    def children(entity: CCMIOID) -> list[tuple[str, CCMIOID]]:
        out = []
        n = ctypes.c_int()
        err = ctypes.c_int(0)
        get_n(ctypes.byref(err), entity.node, ctypes.byref(n))
        idx = ctypes.c_int(0)
        while True:
            err = ctypes.c_int(0)
            child = CCMIOID()
            code = next_c(
                ctypes.byref(err), entity.node, ctypes.byref(idx), ctypes.byref(child)
            )
            if code != 0:
                break
            buf = ctypes.create_string_buffer(33)
            err = ctypes.c_int(0)
            name(ctypes.byref(err), child, buf)
            out.append((buf.value.decode("utf-8", "replace"), child))
        return out

    return children


def _bind_label_and_opt2(ccmio: CCMIO):
    lib = ccmio._lib
    err_p = ctypes.POINTER(ctypes.c_int)
    id_p = ctypes.POINTER(CCMIOID)

    label = getattr(lib, "CCMIOEntityLabel")
    label.restype = ctypes.c_int
    label.argtypes = [
        err_p, CCMIOID, ctypes.POINTER(ctypes.c_int), ctypes.c_char_p
    ]

    read_opt2i = getattr(lib, "CCMIOReadOpt2i")
    read_opt2i.restype = ctypes.c_int
    read_opt2i.argtypes = [
        err_p, CCMIOID, ctypes.c_char_p, ctypes.POINTER(ctypes.c_int),
        ctypes.c_uint, ctypes.c_uint,
    ]

    def entity_labels(entity: CCMIOID) -> list[str]:
        out = []
        for _n, child in _bind_child_iter(ccmio)(entity):
            size = ctypes.c_int()
            buf = ctypes.create_string_buffer(512)
            err = ctypes.c_int(0)
            label(ctypes.byref(err), child, ctypes.byref(size), buf)
            out.append(buf.value.decode("utf-8", "replace"))
        return out

    def read_opt2i_data(parent: CCMIOID, name: str, n: int, width: int):
        out = np.empty(n * width, dtype=np.int32)
        err = ctypes.c_int(0)
        code = read_opt2i(
            ctypes.byref(err), parent, _b(name),
            out.ctypes.data_as(ctypes.POINTER(ctypes.c_int)),
            ctypes.c_uint(0), ctypes.c_uint(0),
        )
        if code != 0:
            raise CCMIOError(f"{name}: {code}")
        return out.reshape(n, width)

    return entity_labels, read_opt2i_data


def _b(value: str) -> bytes:
    return value.encode("utf-8")


def parse_stream(stream: np.ndarray, n_expected: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Split a CCM face stream into (starts, npe, flat vertex ids)."""
    if stream.size == 0:
        return np.zeros(1, np.int64), np.empty(0, np.int64), np.empty(0, np.int64)
    data = stream.tolist()
    n = len(data)
    starts_list = []
    npe_list = []
    p = 0
    while p < n:
        nv = int(data[p])
        if nv < 0 or p + 1 + nv > n:
            break
        starts_list.append(p)
        npe_list.append(nv)
        p += 1 + nv
    starts = np.asarray(starts_list, dtype=np.int64)
    npe = np.asarray(npe_list, dtype=np.int64)
    keep = np.ones(n, dtype=bool)
    keep[starts] = False
    vids = stream[keep].astype(np.int64)
    return starts, npe, vids


def face_stats(stream: np.ndarray, n_expected: int):
    starts, npe, vids = parse_stream(stream, n_expected)
    n_faces = npe.size
    return {
        "n_faces_parsed": int(n_faces),
        "n_expected": int(n_expected),
        "npe_min": int(npe.min()) if n_faces else None,
        "npe_max": int(npe.max()) if n_faces else None,
        "npe_hist": {
            str(int(k)): int(v)
            for k, v in zip(*np.unique(npe, return_counts=True))
        }
        if n_faces
        else {},
        "n_lt3": int((npe < 3).sum()) if n_faces else 0,
        "vids_min": int(vids.min()) if vids.size else None,
        "vids_max": int(vids.max()) if vids.size else None,
        "n_duplicate_vid_in_face": int(
            np.count_nonzero(np.diff(vids) == 0)
        )
        if vids.size
        else 0,
    }


def map_stats(ccmio: CCMIO, map_id: CCMIOID, n: int) -> dict:
    if n == 0:
        return {"n": 0}
    data = ccmio.read_map(map_id, n)
    uniq = np.unique(data)
    linear = bool(
        data.size and np.array_equal(data, np.arange(1, data.size + 1, dtype=np.int32))
    )
    return {
        "n": int(n),
        "min": int(data.min()),
        "max": int(data.max()),
        "n_unique": int(uniq.size),
        "linear_1n": linear,
        "has_zero": bool((data == 0).any()),
        "has_negative": bool((data < 0).any()),
    }


def try_opt1i(ccmio: CCMIO, node: CCMIOID, name: str, n: int):
    try:
        arr = ccmio.read_opt1i(node, name, n)
        return arr
    except Exception:
        return None


def try_opti(ccmio: CCMIO, node: CCMIOID, name: str):
    try:
        return ccmio.read_opti(node, name)
    except Exception:
        return None


def try_optstr(ccmio: CCMIO, node: CCMIOID, name: str):
    try:
        return ccmio.read_optstr(node, name)
    except Exception:
        return None


def cell_topology_stats(ccmio: CCMIO, cells_node: CCMIOID, n_cells: int) -> dict:
    arr = try_opt1i(ccmio, cells_node, "CellTopologyType", n_cells)
    if arr is None:
        return {"present": False}
    return {
        "present": True,
        "min": int(arr.min()),
        "max": int(arr.max()),
        "hist": {
            str(int(k)): int(v)
            for k, v in zip(*np.unique(arr, return_counts=True))
        },
    }


def prostar_face_id_stats(
    ccmio: CCMIO, node: CCMIOID, which: int, n: int
) -> dict:
    """Detect the optional ProstarFaceId array on a faces entity."""
    try:
        _, read_opt2i_data = _bind_label_and_opt2(ccmio)
        if which == K_CCMIO_INTERNAL_FACES:
            arr = read_opt2i_data(node, "ProstarFaceId", n, 2)
            return {
                "present": True,
                "width": 2,
                "min": int(arr.min()) if arr.size else None,
                "max": int(arr.max()) if arr.size else None,
                "has_zero": bool((arr == 0).any()),
            }
        arr = ccmio.read_opt1i(node, "ProstarFaceId", n)
        return {
            "present": True,
            "width": 1,
            "min": int(arr.min()) if arr.size else None,
            "max": int(arr.max()) if arr.size else None,
            "has_zero": bool((arr == 0).any()),
        }
    except Exception:
        return {"present": False}


def face_cells_stats(ccmio: CCMIO, node: CCMIOID, which: int, n: int) -> dict:
    if n == 0:
        return {"n": 0}
    cells = ccmio.read_face_cells(node, which)
    width = cells.shape[1]
    flat = cells.reshape(-1)
    return {
        "n": int(n),
        "width": int(width),
        "min": int(flat.min()),
        "max": int(flat.max()),
        "n_zero": int((flat == 0).sum()),
        "n_negative": int((flat < 0).sum()),
        "n_self_adjacent": int((cells[:, 0] == cells[:, 1]).sum())
        if width == 2
        else None,
    }


def region_face_counts(owner, neigh, n_cells: int) -> dict:
    cnt = np.bincount(
        np.concatenate([owner, neigh[neigh >= 0]]), minlength=n_cells
    )
    lt4 = int((cnt < 4).sum())
    return {
        "per_cell_min": int(cnt.min()),
        "per_cell_max": int(cnt.max()),
        "per_cell_hist": {
            str(int(k)): int(v)
            for k, v in zip(*np.unique(cnt, return_counts=True))
        }
        if n_cells < 200000
        else {},
        "n_cells_lt4_faces": lt4,
    }


def first_entity(ccmio: CCMIO, parent: CCMIOID, etype: int):
    for node in ccmio.iter_entities(parent, etype):
        return node
    return None


def dump(path: str, full: bool = False) -> dict:
    t0 = time.perf_counter()
    ccmio = CCMIO()
    root = ccmio.open_file_readonly(path)
    children = _bind_child_iter(ccmio)
    try:
        info: dict = {
            "path": str(path),
            "version": ccmio.get_version(root),
            "problem": {},
        }
        child_labels, _read_opt2 = _bind_label_and_opt2(ccmio)
        try:
            info["title"] = ccmio.entity_name(root)
        except Exception:
            pass

        root_children = children(root)
        info["root_children"] = [
            {"name": n} for n, _child in root_children
        ]

        # ---- maps -------------------------------------------------------
        maps = {}
        for node in ccmio.iter_entities(root, K_CCMIO_MAP):
            name = ccmio.entity_name(node)
            n, max_id = ccmio.entity_size(node)
            maps[name] = map_stats(ccmio, node, int(n))
            maps[name]["max_id"] = int(max_id)
        info["maps"] = maps

        # ---- vertices ----------------------------------------------------
        try:
            verts = first_entity(ccmio, root, K_CCMIO_VERTICES)
            dims, scale, vmap, coords = ccmio.read_vertices(verts)
            n_verts = int(coords.shape[0])
            info["vertices"] = {
                "dims": int(dims),
                "scale": float(scale),
                "n": n_verts,
                "map": ccmio.entity_name(vmap),
                "bbox_min": coords.min(axis=0).tolist(),
                "bbox_max": coords.max(axis=0).tolist(),
                "n_nan": int((~np.isfinite(coords)).sum()),
                "children": [n for n, _ in children(verts)],
            }
        except Exception as exc:
            info["vertices"] = {"error": str(exc)}

        # ---- state / problem / processor --------------------------------
        try:
            state, problem = ccmio.get_state(root)
            info["state"] = {"problem_type": int(problem.type)}
            procs = []
            for pnode in ccmio.iter_entities(state, K_CCMIO_PROCESSOR):
                try:
                    v, topo, _, _ = ccmio.read_processor(pnode)
                    procs.append(
                        {
                            "vertices": ccmio.entity_name(v),
                            "topology": ccmio.entity_name(topo),
                            "children": [n for n, _ in children(pnode)],
                        }
                    )
                except Exception as exc:
                    procs.append({"error": str(exc)})
            info["state"]["processors"] = procs
        except Exception as exc:
            info["state"] = {"error": str(exc)}

        # ---- topology ----------------------------------------------------
        topo = first_entity(ccmio, root, K_CCMIO_TOPOLOGY)
        if topo is None:
            info["topology_error"] = "no TOPOLOGY entity at root"
            return info
        info["topology_children"] = [n for n, _ in children(topo)]

        cells_node = first_entity(ccmio, topo, K_CCMIO_CELLS)
        cell_map_id, cell_types = ccmio.read_cells(cells_node)
        n_cells = int(cell_types.size)
        info["cells"] = {
            "n": n_cells,
            "map": ccmio.entity_name(cell_map_id),
            "cell_type_ids": {
                "min": int(cell_types.min()) if n_cells else None,
                "max": int(cell_types.max()) if n_cells else None,
                "hist": {
                    str(int(k)): int(v)
                    for k, v in zip(*np.unique(cell_types, return_counts=True))
                }
                if n_cells < 200000
                else {},
            },
            "topology": cell_topology_stats(ccmio, cells_node, n_cells),
            "children": child_labels(cells_node),
        }

        # ---- internal faces ----------------------------------------------
        iface_node = first_entity(ccmio, topo, K_CCMIO_INTERNAL_FACES)
        n_if, if_max = ccmio.entity_size(iface_node)
        n_if = int(n_if)
        ifmap, istream = ccmio.read_faces(iface_node, K_CCMIO_INTERNAL_FACES)
        icells = ccmio.read_face_cells(iface_node, K_CCMIO_INTERNAL_FACES)
        starts, npe, vids = parse_stream(istream, n_if)
        info["internal_faces"] = {
            "n": n_if,
            "max_id": int(if_max),
            "map": ccmio.entity_name(ifmap),
            "stream_size": int(istream.size),
            **face_stats(istream, n_if),
            "face_cells": face_cells_stats(ccmio, iface_node, K_CCMIO_INTERNAL_FACES, n_if),
            "prostar_face_id": prostar_face_id_stats(
                ccmio, iface_node, K_CCMIO_INTERNAL_FACES, n_if
            ),
            "children": child_labels(iface_node),
        }
        if n_if:
            info["internal_faces"]["region_face_counts"] = region_face_counts(
                icells[:, 0] - 1, icells[:, 1] - 1, n_cells
            )

        # ---- boundary faces ----------------------------------------------
        bfaces = {}
        for node in ccmio.iter_entities(topo, K_CCMIO_BOUNDARY_FACES):
            rid = ccmio.entity_index(node)
            n_bf, bf_max = ccmio.entity_size(node)
            n_bf = int(n_bf)
            bmap, bstream = ccmio.read_faces(node, K_CCMIO_BOUNDARY_FACES)
            try:
                bcells = ccmio.read_face_cells(node, K_CCMIO_BOUNDARY_FACES)
                bcells_stats = face_cells_stats(
                    ccmio, node, K_CCMIO_BOUNDARY_FACES, n_bf
                )
            except Exception:
                bcells_stats = {"n": int(n_bf), "error": "no FacesCellData"}
            entry = {
                "n": n_bf,
                "max_id": int(bf_max),
                "map": ccmio.entity_name(bmap),
                "map_stats": map_stats(ccmio, bmap, n_bf),
                "stream_size": int(bstream.size),
                **face_stats(bstream, n_bf),
                "face_cells": bcells_stats,
                "prostar_face_id": prostar_face_id_stats(
                    ccmio, node, K_CCMIO_BOUNDARY_FACES, n_bf
                ),
                "children": child_labels(node),
            }
            bfaces[int(rid)] = entry
        info["boundary_faces"] = bfaces

        # ---- problem description ------------------------------------------
        try:
            cell_types_info = {}
            for node in ccmio.iter_entities(problem, K_CCMIO_CELL_TYPE):
                cid = ccmio.entity_index(node)
                cell_types_info[int(cid)] = {
                    "label": try_optstr(ccmio, node, "Label"),
                    "material": try_optstr(ccmio, node, "MaterialType"),
                    "porosity": try_opti(ccmio, node, "Porosity"),
                    "spin": try_opti(ccmio, node, "Spin"),
                    "group": try_opti(ccmio, node, "Group"),
                    "shell": try_opti(ccmio, node, "Shell"),
                    "children": [n for n, _ in children(node)],
                }
            info["problem"]["cell_types"] = cell_types_info
            region_info = {}
            for node in ccmio.iter_entities(problem, K_CCMIO_BOUNDARY_REGION):
                rid = ccmio.entity_index(node)
                region_info[int(rid)] = {
                    "label": try_optstr(ccmio, node, "Label"),
                    "type": try_optstr(ccmio, node, "BoundaryType"),
                    "children": [n for n, _ in children(node)],
                }
            info["problem"]["boundary_regions"] = region_info
            info["problem"]["children"] = [n for n, _ in children(problem)]
        except Exception as exc:
            info["problem"] = {"error": str(exc)}

        info["elapsed_s"] = round(time.perf_counter() - t0, 1)
        return info
    finally:
        ccmio.close_file(root)


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    full = "--full" in sys.argv
    info = dump(sys.argv[1], full=full)
    print(json.dumps(info, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
