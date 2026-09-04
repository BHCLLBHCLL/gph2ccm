"""Thin ``ctypes`` bindings for the CCMIO C library (libccmio / ccmio.dll).

The CCMIO library is the I/O layer used by STAR-CCM+/STAR-CD for the legacy
``.ccm`` file format.  This module binds the subset of the API needed to
write a mesh (vertices, topology, cells, problem description) and to read a
file back for verification.

The library is located in the following order:

1. ``GPH2CCM_CCMIO_DLL`` environment variable
2. the ``ccmio.dll`` shipped inside a local STAR-CCM+ installation
3. ``ccmio.dll`` / ``libccmio.so`` / ``libccmio.dylib`` on the DLL search path

API details follow libccmio-2.6.1 (``libccmio/ccmio.h``,
``libccmio/ccmiotypes.h``, ``libccmio/ccmioutility.h``).
"""

from __future__ import annotations

import ctypes
import glob
import os
from pathlib import Path
import shutil
import sys
from typing import Optional

import numpy as np

# ---------------------------------------------------------------------------
# Constants (ccmiotypes.h)
# ---------------------------------------------------------------------------

K_CCMIO_NO_ERR = 0
K_CCMIO_NO_FILE_ERR = 1
K_CCMIO_PERMISSION_ERR = 2
K_CCMIO_CORRUPT_FILE_ERR = 3
K_CCMIO_BAD_LINK_ERR = 4
K_CCMIO_NO_NODE_ERR = 5
K_CCMIO_DUPLICATE_NODE_ERR = 6
K_CCMIO_WRONG_DATA_TYPE_ERR = 7
K_CCMIO_NO_DATA_ERR = 8
K_CCMIO_WRONG_PARENT_ERR = 9
K_CCMIO_BAD_PARAMETER_ERR = 10
K_CCMIO_NO_MEMORY_ERR = 11
K_CCMIO_IO_ERR = 12
K_CCMIO_TOO_MANY_FACES_ERR = 13
K_CCMIO_VERSION_ERR = 14
K_CCMIO_ARRAY_DIMENSION_TOO_LARGE_ERR = 15
K_CCMIO_INTERNAL_ERR = 16

ERROR_NAMES = {
    K_CCMIO_NO_ERR: "kCCMIONoErr",
    K_CCMIO_NO_FILE_ERR: "kCCMIONoFileErr",
    K_CCMIO_PERMISSION_ERR: "kCCMIOPermissionErr",
    K_CCMIO_CORRUPT_FILE_ERR: "kCCMIOCorruptFileErr",
    K_CCMIO_BAD_LINK_ERR: "kCCMIOBadLinkErr",
    K_CCMIO_NO_NODE_ERR: "kCCMIONoNodeErr",
    K_CCMIO_DUPLICATE_NODE_ERR: "kCCMIODuplicateNodeErr",
    K_CCMIO_WRONG_DATA_TYPE_ERR: "kCCMIOWrongDataTypeErr",
    K_CCMIO_NO_DATA_ERR: "kCCMIONoDataErr",
    K_CCMIO_WRONG_PARENT_ERR: "kCCMIOWrongParentErr",
    K_CCMIO_BAD_PARAMETER_ERR: "kCCMIOBadParameterErr",
    K_CCMIO_NO_MEMORY_ERR: "kCCMIONoMemoryErr",
    K_CCMIO_IO_ERR: "kCCMIOIOErr",
    K_CCMIO_TOO_MANY_FACES_ERR: "kCCMIOTooManyFacesErr",
    K_CCMIO_VERSION_ERR: "kCCMIOVersionErr",
    K_CCMIO_ARRAY_DIMENSION_TOO_LARGE_ERR: "kCCMIOArrayDimensionToLargeErr",
    K_CCMIO_INTERNAL_ERR: "kCCMIOInternalErr",
}

K_CCMIO_READ = 0
K_CCMIO_WRITE = 1

K_CCMIO_START = 0
K_CCMIO_END = 0

K_CCMIO_MAX_STRING_LENGTH = 32
K_CCMIO_PROSTAR_SHORT_NAME_LENGTH = 8

# CCMIOEntity
K_CCMIO_NULL = -1
K_CCMIO_MAP = 0
K_CCMIO_VERTICES = 1
K_CCMIO_TOPOLOGY = 2
K_CCMIO_INTERNAL_FACES = 3
K_CCMIO_BOUNDARY_FACES = 4
K_CCMIO_CELLS = 5
K_CCMIO_PROBLEM_DESCRIPTION = 6
K_CCMIO_FIELD_SET = 7
K_CCMIO_FIELD = 8
K_CCMIO_FIELD_DATA = 9
K_CCMIO_STATE = 10
K_CCMIO_PROCESSOR = 11
K_CCMIO_CELL_TYPE = 12
K_CCMIO_BOUNDARY_REGION = 13
K_CCMIO_LAGRANGIAN_DATA = 14
K_CCMIO_INTERFACES = 15
K_CCMIO_FIELD_PHASE = 16
K_CCMIO_RESTART = 17
K_CCMIO_RESTART_DATA = 18
K_CCMIO_REFERENCE_DATA = 19
K_CCMIO_MODEL_CONSTANTS = 20
K_CCMIO_PROSTAR_SET = 21
K_CCMIO_MAX_ENTITY = 22

# CCMIODataLocation
K_CCMIO_VERTEX = 0
K_CCMIO_CELL = 1
K_CCMIO_FACE = 2

# CCMIODimensionality
K_CCMIO_DIM_NULL = 0
K_CCMIO_SCALAR = 1
K_CCMIO_VECTOR = 2
K_CCMIO_TENSOR = 3

# CCMIOComponent (ccmiotypes.h)
K_CCMIO_VECTOR_X = 0
K_CCMIO_VECTOR_Y = 1
K_CCMIO_VECTOR_Z = 2


class CCMIOError(Exception):
    """Raised when a CCMIO call reports an error."""


class CCMIONode(ctypes.Structure):
    _fields_ = [
        ("node", ctypes.c_double),
        ("parent", ctypes.c_double),
    ]


class CCMIOID(ctypes.Structure):
    _fields_ = [
        ("root", CCMIONode),
        ("node", CCMIONode),
        ("id", ctypes.c_int),
        ("type", ctypes.c_int),
        ("version", ctypes.c_int),
    ]


def _b(value: Optional[str | bytes]) -> Optional[bytes]:
    if value is None:
        return None
    if isinstance(value, bytes):
        return value
    return value.encode("utf-8")


def _i32(array: np.ndarray) -> np.ndarray:
    """Return a contiguous ``int32`` view/copy of *array*."""
    arr = np.asarray(array)
    if arr.dtype != np.int32:
        arr = arr.astype(np.int32)
    return np.ascontiguousarray(arr)


def _f32(array: np.ndarray) -> np.ndarray:
    """Return a contiguous ``float32`` view/copy of *array*."""
    arr = np.asarray(array)
    if arr.dtype != np.float32:
        arr = arr.astype(np.float32)
    return np.ascontiguousarray(arr)


def find_ccmio_library() -> Path:
    """Locate a usable CCMIO shared library."""
    env = os.environ.get("GPH2CCM_CCMIO_DLL")
    if env:
        p = Path(env).expanduser()
        if p.is_file():
            return p.resolve()
        raise FileNotFoundError(
            f"GPH2CCM_CCMIO_DLL points to a missing file: {p}"
        )

    candidates: list[Path] = []
    if sys.platform == "win32":
        for base in ("C:\\", "D:\\"):
            candidates += [
                Path(p)
                for p in glob.glob(
                    base + r"Program Files\Siemens\*\STAR-CCM+*\star\lib\win64\*\lib\ccmio.dll"
                )
            ]
        candidates += [
            Path(p)
            for p in glob.glob(
                base + r"Program Files (x86)\Siemens\*\STAR-CCM+*\star\lib\win64\*\lib\ccmio.dll"
            )
            for base in ("C:\\", "D:\\")
        ]
        candidates += [Path(p) for p in glob.glob(r"C:\Siemens\*\STAR-CCM+*\star\lib\win64\*\lib\ccmio.dll")]

    names = ("ccmio.dll", "libccmio.so", "libccmio.dylib", "ccmio.so")
    for name in names:
        hit = shutil.which(name)
        if hit:
            candidates.append(Path(hit))

    # Deduplicate, keep existing files only
    seen = set()
    for p in candidates:
        key = str(p).lower()
        if key in seen:
            continue
        seen.add(key)
        if p.is_file():
            return p.resolve()

    raise FileNotFoundError(
        "Cannot find a CCMIO library. Set GPH2CCM_CCMIO_DLL to the "
        "ccmio.dll/libccmio.so path (e.g. the ccmio.dll inside a STAR-CCM+ "
        "installation), or add libccmio to the DLL search path."
    )


class CCMIO:
    """ctypes wrapper around the CCMIO C API."""

    def __init__(self, lib_path: Optional[str | Path] = None):
        self.path = Path(lib_path).resolve() if lib_path else find_ccmio_library()
        self._lib = ctypes.CDLL(str(self.path))
        self._bind()

    # -- binding helpers ----------------------------------------------------

    def _bind(self) -> None:
        lib = self._lib
        err_p = ctypes.POINTER(ctypes.c_int)
        id_p = ctypes.POINTER(CCMIOID)
        char_p = ctypes.c_char_p
        int_p = ctypes.POINTER(ctypes.c_int)
        uint = ctypes.c_uint
        uint_p = ctypes.POINTER(ctypes.c_uint)
        float_p = ctypes.POINTER(ctypes.c_float)

        def bind(name: str, restype, *argtypes):
            fn = getattr(lib, name)
            fn.restype = restype
            fn.argtypes = list(argtypes)
            return fn

        # File / state
        self._CCMIOOpenFile = bind(
            "CCMIOOpenFile", ctypes.c_int, err_p, char_p, ctypes.c_int, id_p
        )
        self._CCMIOCloseFile = bind("CCMIOCloseFile", ctypes.c_int, err_p, CCMIOID)
        self._CCMIOCompress = bind("CCMIOCompress", ctypes.c_int, err_p, char_p)
        self._CCMIOGetVersion = bind("CCMIOGetVersion", ctypes.c_int, err_p, CCMIONode, int_p)
        self._CCMIOSetTitle = bind("CCMIOSetTitle", ctypes.c_int, err_p, CCMIONode, char_p)

        # Entity creation / navigation
        self._CCMIONewEntity = bind(
            "CCMIONewEntity", ctypes.c_int, err_p, CCMIOID, ctypes.c_int, char_p, id_p
        )
        self._CCMIONewIndexedEntity = bind(
            "CCMIONewIndexedEntity", ctypes.c_int, err_p, CCMIOID, ctypes.c_int,
            ctypes.c_int, char_p, id_p,
        )
        self._CCMIOGetEntity = bind(
            "CCMIOGetEntity", ctypes.c_int, err_p, CCMIOID, ctypes.c_int,
            ctypes.c_int, id_p,
        )
        self._CCMIONextEntity = bind(
            "CCMIONextEntity", ctypes.c_int, err_p, CCMIOID, ctypes.c_int, int_p, id_p
        )
        self._CCMIOEntitySize = bind(
            "CCMIOEntitySize", ctypes.c_int, err_p, CCMIOID, uint_p, uint_p
        )
        self._CCMIOEntityName = bind(
            "CCMIOEntityName", ctypes.c_int, err_p, CCMIOID, char_p
        )
        self._CCMIOSetName = bind(
            "CCMIOSetName", ctypes.c_int, err_p, CCMIONode, char_p
        )
        self._CCMIOCreateNode = bind(
            "CCMIOCreateNode", ctypes.c_int, err_p, CCMIONode, ctypes.c_int,
            char_p, char_p, ctypes.POINTER(CCMIONode),
        )
        self._CCMIOWriteNodestr = bind(
            "CCMIOWriteNodestr", ctypes.c_int, err_p, CCMIONode, char_p, char_p
        )
        self._CCMIOWriteNodei = bind(
            "CCMIOWriteNodei", ctypes.c_int, err_p, CCMIONode, char_p,
            ctypes.c_int,
        )
        self._CCMIOGetEntityIndex = bind(
            "CCMIOGetEntityIndex", ctypes.c_int, err_p, CCMIOID, int_p
        )

        # State / processor
        self._CCMIONewState = bind(
            "CCMIONewState", ctypes.c_int, err_p, CCMIOID, char_p, id_p, char_p, id_p
        )
        self._CCMIOGetState = bind(
            "CCMIOGetState", ctypes.c_int, err_p, CCMIOID, char_p, id_p, id_p
        )
        self._CCMIOWriteState = bind(
            "CCMIOWriteState", ctypes.c_int, err_p, CCMIOID, CCMIOID, char_p
        )
        self._CCMIOClearProcessor = bind(
            "CCMIOClearProcessor", ctypes.c_int, err_p, CCMIOID, CCMIOID,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        )
        self._CCMIOWriteProcessor = bind(
            "CCMIOWriteProcessor", ctypes.c_int, err_p, CCMIOID, char_p, id_p,
            char_p, id_p, char_p, id_p, char_p, id_p,
        )
        self._CCMIOReadProcessor = bind(
            "CCMIOReadProcessor", ctypes.c_int, err_p, CCMIOID, id_p, id_p, id_p, id_p
        )

        # Optional nodes
        self._CCMIOWriteOptstr = bind(
            "CCMIOWriteOptstr", ctypes.c_int, err_p, CCMIOID, char_p, char_p
        )
        self._CCMIOWriteOpti = bind(
            "CCMIOWriteOpti", ctypes.c_int, err_p, CCMIOID, char_p, ctypes.c_int
        )
        self._CCMIOWriteOptf = bind(
            "CCMIOWriteOptf", ctypes.c_int, err_p, CCMIOID, char_p, ctypes.c_float
        )
        self._CCMIOWriteOpt1i = bind(
            "CCMIOWriteOpt1i", ctypes.c_int, err_p, CCMIOID, char_p, uint,
            int_p, uint, uint,
        )
        self._CCMIOReadOptstr = bind(
            "CCMIOReadOptstr", ctypes.c_int, err_p, CCMIOID, char_p, int_p, char_p
        )
        self._CCMIOReadOpti = bind(
            "CCMIOReadOpti", ctypes.c_int, err_p, CCMIOID, char_p, int_p
        )
        self._CCMIOReadOpt1i = bind(
            "CCMIOReadOpt1i", ctypes.c_int, err_p, CCMIOID, char_p, int_p,
            uint, uint,
        )

        # Mesh data
        self._CCMIOWriteMap = bind(
            "CCMIOWriteMap", ctypes.c_int, err_p, CCMIOID, uint, uint, int_p,
            uint, uint,
        )
        self._CCMIOReadMap = bind(
            "CCMIOReadMap", ctypes.c_int, err_p, CCMIOID, int_p, uint, uint
        )
        self._CCMIOWriteVerticesf = bind(
            "CCMIOWriteVerticesf", ctypes.c_int, err_p, CCMIOID, ctypes.c_int,
            ctypes.c_float, CCMIOID, float_p, uint, uint,
        )
        self._CCMIOReadVerticesf = bind(
            "CCMIOReadVerticesf", ctypes.c_int, err_p, CCMIOID, int_p,
            float_p, id_p, float_p, uint, uint,
        )
        self._CCMIOWriteCells = bind(
            "CCMIOWriteCells", ctypes.c_int, err_p, CCMIOID, CCMIOID, int_p,
            uint, uint,
        )
        self._CCMIOReadCells = bind(
            "CCMIOReadCells", ctypes.c_int, err_p, CCMIOID, id_p, int_p, uint, uint
        )
        self._CCMIOWriteFaces = bind(
            "CCMIOWriteFaces", ctypes.c_int, err_p, CCMIOID, ctypes.c_int,
            CCMIOID, uint, int_p, uint, uint,
        )
        self._CCMIOReadFaces = bind(
            "CCMIOReadFaces", ctypes.c_int, err_p, CCMIOID, ctypes.c_int, id_p,
            uint_p, int_p, uint, uint,
        )
        self._CCMIOWriteFaceCells = bind(
            "CCMIOWriteFaceCells", ctypes.c_int, err_p, CCMIOID, ctypes.c_int,
            CCMIOID, int_p, uint, uint,
        )
        self._CCMIOReadFaceCells = bind(
            "CCMIOReadFaceCells", ctypes.c_int, err_p, CCMIOID, ctypes.c_int,
            int_p, uint, uint,
        )
        self._CCMIOV2WriteFaceCells = bind(
            "CCMIOV2WriteFaceCells", ctypes.c_int, err_p, CCMIOID, ctypes.c_int,
            uint, int_p, uint, uint,
        )

        # Fields (C2: actual solution data; recipes from docs/examples/
        # writeexample.cpp of libccmio-2.6.1)
        self._CCMIONewField = bind(
            "CCMIONewField", ctypes.c_int, err_p, CCMIOID, char_p, char_p,
            ctypes.c_int, id_p,
        )
        self._CCMIOReadField = bind(
            "CCMIOReadField", ctypes.c_int, err_p, CCMIOID, char_p, char_p,
            int_p, int_p,
        )
        self._CCMIOWriteFieldDataf = bind(
            "CCMIOWriteFieldDataf", ctypes.c_int, err_p, CCMIOID, CCMIOID,
            ctypes.c_int, float_p, uint, uint,
        )
        self._CCMIOReadFieldDataf = bind(
            "CCMIOReadFieldDataf", ctypes.c_int, err_p, CCMIOID, id_p, int_p,
            float_p, uint, uint,
        )
        self._CCMIOWriteConstantFieldDataf = bind(
            "CCMIOWriteConstantFieldDataf", ctypes.c_int, err_p, CCMIOID,
            CCMIOID, ctypes.c_int, ctypes.c_float,
        )
        self._CCMIOWriteMultiDimensionalFieldData = bind(
            "CCMIOWriteMultiDimensionalFieldData", ctypes.c_int, err_p,
            CCMIOID, ctypes.c_int, CCMIOID,
        )
        self._CCMIOReadMultiDimensionalFieldData = bind(
            "CCMIOReadMultiDimensionalFieldData", ctypes.c_int, err_p,
            CCMIOID, ctypes.c_int, id_p,
        )
        self._CCMIOWriteRestartInfo = bind(
            "CCMIOWriteRestartInfo", ctypes.c_int, err_p, CCMIOID, char_p,
            ctypes.c_int, ctypes.c_float, char_p, ctypes.c_float,
        )
        self._CCMIOReadRestartInfo = bind(
            "CCMIOReadRestartInfo", ctypes.c_int, err_p, CCMIOID, char_p,
            int_p, float_p, char_p, float_p,
        )

    # -- error handling -----------------------------------------------------

    @staticmethod
    def _check(code: int, ctx: str) -> None:
        if code != K_CCMIO_NO_ERR:
            raise CCMIOError(
                f"{ctx} failed: {code} ({ERROR_NAMES.get(code, 'unknown')})"
            )

    def _err(self, ctx: str) -> ctypes.c_int:
        return ctypes.c_int(K_CCMIO_NO_ERR)

    # -- file / state -------------------------------------------------------

    def open_file(self, path: str | Path) -> CCMIOID:
        err = ctypes.c_int(K_CCMIO_NO_ERR)
        root = CCMIOID()
        code = self._CCMIOOpenFile(
            ctypes.byref(err),
            _b(str(path)),
            K_CCMIO_WRITE,
            ctypes.byref(root),
        )
        self._check(code, f"CCMIOOpenFile({path})")
        return root

    def open_file_readonly(self, path: str | Path) -> CCMIOID:
        """Open an existing file for reading (used by tests/verification)."""
        err = ctypes.c_int(K_CCMIO_NO_ERR)
        root = CCMIOID()
        code = self._CCMIOOpenFile(
            ctypes.byref(err),
            _b(str(path)),
            K_CCMIO_READ,
            ctypes.byref(root),
        )
        self._check(code, f"CCMIOOpenFile(read, {path})")
        return root

    def close_file(self, root: CCMIOID) -> None:
        err = ctypes.c_int(K_CCMIO_NO_ERR)
        code = self._CCMIOCloseFile(ctypes.byref(err), root)
        self._check(code, "CCMIOCloseFile")

    def compress(self, path: str | Path) -> None:
        err = ctypes.c_int(K_CCMIO_NO_ERR)
        code = self._CCMIOCompress(ctypes.byref(err), _b(str(path)))
        self._check(code, "CCMIOCompress")

    def get_version(self, root: CCMIOID) -> int:
        err = ctypes.c_int(K_CCMIO_NO_ERR)
        version = ctypes.c_int()
        code = self._CCMIOGetVersion(ctypes.byref(err), root.root, ctypes.byref(version))
        self._check(code, "CCMIOGetVersion")
        return version.value

    def set_title(self, root: CCMIOID, title: str) -> None:
        err = ctypes.c_int(K_CCMIO_NO_ERR)
        code = self._CCMIOSetTitle(ctypes.byref(err), root.root, _b(title))
        self._check(code, "CCMIOSetTitle")

    def new_state(
        self, root: CCMIOID, name: str = "default", description: Optional[str] = None
    ) -> CCMIOID:
        err = ctypes.c_int(K_CCMIO_NO_ERR)
        state = CCMIOID()
        code = self._CCMIONewState(
            ctypes.byref(err), root, _b(name), None, _b(description), ctypes.byref(state)
        )
        self._check(code, "CCMIONewState")
        return state

    def get_state(self, root: CCMIOID, name: str = "default") -> tuple[CCMIOID, CCMIOID]:
        err = ctypes.c_int(K_CCMIO_NO_ERR)
        problem = CCMIOID()
        state = CCMIOID()
        code = self._CCMIOGetState(
            ctypes.byref(err), root, _b(name), ctypes.byref(problem), ctypes.byref(state)
        )
        self._check(code, "CCMIOGetState")
        return state, problem

    def write_state(
        self, state: CCMIOID, problem: CCMIOID, description: Optional[str] = None
    ) -> None:
        err = ctypes.c_int(K_CCMIO_NO_ERR)
        code = self._CCMIOWriteState(
            ctypes.byref(err), state, problem, _b(description)
        )
        self._check(code, "CCMIOWriteState")

    # -- entities -----------------------------------------------------------

    def new_entity(
        self, parent: CCMIOID, etype: int, description: Optional[str] = None
    ) -> CCMIOID:
        err = ctypes.c_int(K_CCMIO_NO_ERR)
        out = CCMIOID()
        code = self._CCMIONewEntity(
            ctypes.byref(err), parent, etype, _b(description), ctypes.byref(out)
        )
        self._check(code, "CCMIONewEntity")
        return out

    def new_indexed_entity(
        self, parent: CCMIOID, etype: int, index: int, description: Optional[str] = None
    ) -> CCMIOID:
        err = ctypes.c_int(K_CCMIO_NO_ERR)
        out = CCMIOID()
        code = self._CCMIONewIndexedEntity(
            ctypes.byref(err), parent, etype, index, _b(description), ctypes.byref(out)
        )
        self._check(code, "CCMIONewIndexedEntity")
        return out

    def get_entity(self, parent: CCMIOID, etype: int, index: int = 0) -> CCMIOID:
        err = ctypes.c_int(K_CCMIO_NO_ERR)
        out = CCMIOID()
        code = self._CCMIOGetEntity(
            ctypes.byref(err), parent, etype, index, ctypes.byref(out)
        )
        self._check(code, "CCMIOGetEntity")
        return out

    def next_entity(self, parent: CCMIOID, etype: int, start: int = 0) -> Optional[CCMIOID]:
        err = ctypes.c_int(K_CCMIO_NO_ERR)
        counter = ctypes.c_int(start)
        out = CCMIOID()
        code = self._CCMIONextEntity(
            ctypes.byref(err), parent, etype, ctypes.byref(counter), ctypes.byref(out)
        )
        if code == K_CCMIO_NO_NODE_ERR:
            return None
        self._check(code, "CCMIONextEntity")
        return out

    def iter_entities(self, parent: CCMIOID, etype: int):
        """Yield all child entities of *etype* under *parent*."""
        counter = ctypes.c_int(0)
        while True:
            err = ctypes.c_int(K_CCMIO_NO_ERR)
            out = CCMIOID()
            code = self._CCMIONextEntity(
                ctypes.byref(err), parent, etype, ctypes.byref(counter), ctypes.byref(out)
            )
            if code == K_CCMIO_NO_NODE_ERR:
                return
            self._check(code, "CCMIONextEntity")
            yield out

    def entity_size(self, entity: CCMIOID) -> tuple[int, int]:
        err = ctypes.c_int(K_CCMIO_NO_ERR)
        n = ctypes.c_uint()
        max_id = ctypes.c_uint()
        code = self._CCMIOEntitySize(
            ctypes.byref(err), entity, ctypes.byref(n), ctypes.byref(max_id)
        )
        self._check(code, "CCMIOEntitySize")
        return int(n.value), int(max_id.value)

    def entity_name(self, entity: CCMIOID) -> str:
        buf = ctypes.create_string_buffer(K_CCMIO_MAX_STRING_LENGTH + 1)
        err = ctypes.c_int(K_CCMIO_NO_ERR)
        code = self._CCMIOEntityName(ctypes.byref(err), entity, buf)
        self._check(code, "CCMIOEntityName")
        return buf.value.decode("utf-8", errors="replace")

    def set_name(self, entity: CCMIOID, name: str) -> None:
        err = ctypes.c_int(K_CCMIO_NO_ERR)
        code = self._CCMIOSetName(
            ctypes.byref(err), entity.node, _b(name)
        )
        self._check(code, f"CCMIOSetName({name})")

    def create_node(
        self, parent: CCMIONode, name: str, label: str, open_dup: bool = True
    ) -> CCMIONode:
        err = ctypes.c_int(K_CCMIO_NO_ERR)
        out = CCMIONode()
        code = self._CCMIOCreateNode(
            ctypes.byref(err), parent, int(open_dup),
            _b(name), _b(label), ctypes.byref(out),
        )
        self._check(code, f"CCMIOCreateNode({name})")
        return out

    def write_nodestr(self, parent: CCMIONode, name: str, value: str) -> None:
        err = ctypes.c_int(K_CCMIO_NO_ERR)
        code = self._CCMIOWriteNodestr(
            ctypes.byref(err), parent, _b(name), _b(value)
        )
        self._check(code, f"CCMIOWriteNodestr({name})")

    def write_nodei(self, parent: CCMIONode, name: str, value: int) -> None:
        err = ctypes.c_int(K_CCMIO_NO_ERR)
        code = self._CCMIOWriteNodei(
            ctypes.byref(err), parent, _b(name), value
        )
        self._check(code, f"CCMIOWriteNodei({name})")

    def entity_index(self, entity: CCMIOID) -> int:
        err = ctypes.c_int(K_CCMIO_NO_ERR)
        out = ctypes.c_int()
        code = self._CCMIOGetEntityIndex(ctypes.byref(err), entity, ctypes.byref(out))
        self._check(code, "CCMIOGetEntityIndex")
        return out.value

    # -- processor ----------------------------------------------------------

    def new_processor(self, state: CCMIOID) -> CCMIOID:
        existing = self.next_entity(state, K_CCMIO_PROCESSOR, 0)
        if existing is not None:
            return existing
        return self.new_entity(state, K_CCMIO_PROCESSOR)

    def clear_processor(self, state: CCMIOID, processor: CCMIOID) -> None:
        err = ctypes.c_int(K_CCMIO_NO_ERR)
        code = self._CCMIOClearProcessor(
            ctypes.byref(err), state, processor, 1, 1, 1, 1, 1
        )
        self._check(code, "CCMIOClearProcessor")

    def write_processor(
        self,
        processor: CCMIOID,
        vertices: Optional[CCMIOID] = None,
        topology: Optional[CCMIOID] = None,
        initial_field: Optional[CCMIOID] = None,
        solution: Optional[CCMIOID] = None,
    ) -> None:
        err = ctypes.c_int(K_CCMIO_NO_ERR)
        code = self._CCMIOWriteProcessor(
            ctypes.byref(err),
            processor,
            None,
            ctypes.byref(vertices) if vertices is not None else None,
            None,
            ctypes.byref(topology) if topology is not None else None,
            None,
            ctypes.byref(initial_field) if initial_field is not None else None,
            None,
            ctypes.byref(solution) if solution is not None else None,
        )
        self._check(code, "CCMIOWriteProcessor")

    def read_processor(self, processor: CCMIOID) -> tuple[CCMIOID, CCMIOID, CCMIOID, CCMIOID]:
        """Read the vertices/topology entities referenced by *processor*.

        Mesh-only files have no initial-field or solution field set, so those
        two outputs are intentionally not requested (the C API returns
        ``kCCMIONoNodeErr`` when a referenced node is absent).
        """
        err = ctypes.c_int(K_CCMIO_NO_ERR)
        vertices = CCMIOID()
        topology = CCMIOID()
        code = self._CCMIOReadProcessor(
            ctypes.byref(err),
            processor,
            ctypes.byref(vertices),
            ctypes.byref(topology),
            None,
            None,
        )
        self._check(code, "CCMIOReadProcessor")
        return vertices, topology, CCMIOID(), CCMIOID()

    # -- fields (C2: actual solution data) -----------------------------------

    def new_field(
        self, phase: CCMIOID, name: str, short_name: str, dim: int
    ) -> CCMIOID:
        """Create a Field under the FieldPhase *phase* (CCMIONewField)."""
        err = ctypes.c_int(K_CCMIO_NO_ERR)
        out = CCMIOID()
        code = self._CCMIONewField(
            ctypes.byref(err), phase, _b(name), _b(short_name), dim,
            ctypes.byref(out),
        )
        self._check(code, f"CCMIONewField({name})")
        return out

    def read_field(self, field: CCMIOID) -> tuple[str, str, int]:
        """Return ``(name, short_name, dim)`` of a Field entity."""
        name = ctypes.create_string_buffer(K_CCMIO_MAX_STRING_LENGTH + 1)
        short = ctypes.create_string_buffer(K_CCMIO_PROSTAR_SHORT_NAME_LENGTH + 1)
        dim = ctypes.c_int()
        err = ctypes.c_int(K_CCMIO_NO_ERR)
        code = self._CCMIOReadField(
            ctypes.byref(err), field, name, short, ctypes.byref(dim), None
        )
        self._check(code, "CCMIOReadField")
        return (
            name.value.decode("utf-8", errors="replace"),
            short.value.decode("utf-8", errors="replace"),
            int(dim.value),
        )

    def write_field_dataf(
        self, field: CCMIOID, map_id: CCMIOID, location: int, data: np.ndarray
    ) -> None:
        """Write float32 per-entity data for *field* (CCMIOWriteFieldDataf).

        Creates the FieldData child entity itself, mirroring the official
        ``writeexample.cpp`` flow (``CCMIONewEntity(field, kCCMIOFieldData)``
        followed by ``CCMIOWriteFieldDataf``).
        """
        data = np.ascontiguousarray(data, dtype=np.float32)
        field_data = self.new_entity(field, K_CCMIO_FIELD_DATA)
        err = ctypes.c_int(K_CCMIO_NO_ERR)
        code = self._CCMIOWriteFieldDataf(
            ctypes.byref(err),
            field_data,
            map_id,
            location,
            data.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
            K_CCMIO_START,
            K_CCMIO_END,
        )
        self._check(code, "CCMIOWriteFieldDataf")

    def write_constant_field_dataf(
        self, field: CCMIOID, map_id: CCMIOID, location: int, value: float
    ) -> None:
        """Write a constant field value (CCMIOWriteConstantFieldDataf)."""
        field_data = self.new_entity(field, K_CCMIO_FIELD_DATA)
        err = ctypes.c_int(K_CCMIO_NO_ERR)
        code = self._CCMIOWriteConstantFieldDataf(
            ctypes.byref(err), field_data, map_id, location, ctypes.c_float(value)
        )
        self._check(code, "CCMIOWriteConstantFieldDataf")

    def link_vector_component(
        self, vector_field: CCMIOID, component: int, scalar_field: CCMIOID
    ) -> None:
        """Attach *scalar_field* as one X/Y/Z component of *vector_field*."""
        err = ctypes.c_int(K_CCMIO_NO_ERR)
        code = self._CCMIOWriteMultiDimensionalFieldData(
            ctypes.byref(err), vector_field, component, scalar_field
        )
        self._check(code, "CCMIOWriteMultiDimensionalFieldData")

    def read_vector_component(self, vector_field: CCMIOID, component: int) -> CCMIOID:
        """Return the scalar Field holding one X/Y/Z component (readexample.cpp)."""
        out = CCMIOID()
        err = ctypes.c_int(K_CCMIO_NO_ERR)
        code = self._CCMIOReadMultiDimensionalFieldData(
            ctypes.byref(err), vector_field, component, ctypes.byref(out)
        )
        self._check(code, "CCMIOReadMultiDimensionalFieldData")
        return out

    def read_field_dataf(self, field: CCMIOID, n: int) -> np.ndarray:
        """Read back *n* float32 values of a Field's FieldData (round-trip).

        Follows ``readexample.cpp``: walk to the FieldData child with
        ``CCMIONextEntity`` (never ``CCMIOGetEntity``), then read.
        """
        field_data = self.next_entity(field, K_CCMIO_FIELD_DATA, 0)
        if field_data is None:
            raise CCMIOError("field has no FieldData child")
        out = np.empty(n, dtype=np.float32)
        map_id = CCMIOID()
        loc = ctypes.c_int()
        err = ctypes.c_int(K_CCMIO_NO_ERR)
        code = self._CCMIOReadFieldDataf(
            ctypes.byref(err),
            field_data,
            ctypes.byref(map_id),
            ctypes.byref(loc),
            out.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
            K_CCMIO_START,
            K_CCMIO_END,
        )
        self._check(code, "CCMIOReadFieldDataf")
        return out

    def write_restart_info(
        self,
        fieldset: CCMIOID,
        solver_name: str = "gph2ccm",
        iteration: int = 0,
        time: float = 0.0,
        time_units: Optional[str] = None,
        start_angle: float = 0.0,
    ) -> None:
        """Write the solution restart node (E2: iteration/time labelling).

        Creates the ``kCCMIORestart`` child under *fieldset* (the processor's
        solution slot) so STAR-CCM+ can display iteration / time for the
        imported post data.
        """
        restart = self.new_entity(fieldset, K_CCMIO_RESTART)
        err = ctypes.c_int(K_CCMIO_NO_ERR)
        code = self._CCMIOWriteRestartInfo(
            ctypes.byref(err),
            restart,
            _b(solver_name),
            ctypes.c_int(iteration),
            ctypes.c_float(time),
            _b(time_units) if time_units else None,
            ctypes.c_float(start_angle),
        )
        self._check(code, "CCMIOWriteRestartInfo")

    def read_restart_info(self, fieldset: CCMIOID) -> dict:
        """Read back the restart node as a dict (absent node -> ``{}``)."""
        restart = self.next_entity(fieldset, K_CCMIO_RESTART, 0)
        if restart is None:
            return {}
        name = ctypes.create_string_buffer(K_CCMIO_MAX_STRING_LENGTH + 1)
        units = ctypes.create_string_buffer(K_CCMIO_MAX_STRING_LENGTH + 1)
        iteration = ctypes.c_int()
        time = ctypes.c_float()
        start_angle = ctypes.c_float()
        err = ctypes.c_int(K_CCMIO_NO_ERR)
        code = self._CCMIOReadRestartInfo(
            ctypes.byref(err),
            restart,
            name,
            ctypes.byref(iteration),
            ctypes.byref(time),
            units,
            ctypes.byref(start_angle),
        )
        self._check(code, "CCMIOReadRestartInfo")
        return {
            "solver_name": name.value.decode("utf-8", errors="replace"),
            "iteration": int(iteration.value),
            "time": float(time.value),
            "time_units": units.value.decode("utf-8", errors="replace"),
            "start_angle": float(start_angle.value),
        }

    # -- optional nodes -----------------------------------------------------

    def write_optstr(self, parent: CCMIOID, name: str, value: str) -> None:
        err = ctypes.c_int(K_CCMIO_NO_ERR)
        code = self._CCMIOWriteOptstr(
            ctypes.byref(err), parent, _b(name), _b(value)
        )
        self._check(code, f"CCMIOWriteOptstr({name})")

    def write_opti(self, parent: CCMIOID, name: str, value: int) -> None:
        err = ctypes.c_int(K_CCMIO_NO_ERR)
        code = self._CCMIOWriteOpti(ctypes.byref(err), parent, _b(name), value)
        self._check(code, f"CCMIOWriteOpti({name})")

    def write_optf(self, parent: CCMIOID, name: str, value: float) -> None:
        err = ctypes.c_int(K_CCMIO_NO_ERR)
        code = self._CCMIOWriteOptf(ctypes.byref(err), parent, _b(name), value)
        self._check(code, f"CCMIOWriteOptf({name})")

    def write_opt1i(
        self,
        parent: CCMIOID,
        name: str,
        data: np.ndarray,
        total: int,
        start: int = 0,
        end: Optional[int] = None,
    ) -> None:
        """Write part of a 1-D int array as an optional child node.

        ``total`` is the final array length; ``data`` is the contiguous chunk
        starting at global index ``start`` (same convention as
        ``CCMIOWriteCells``).
        """
        arr = _i32(data)
        err = ctypes.c_int(K_CCMIO_NO_ERR)
        code = self._CCMIOWriteOpt1i(
            ctypes.byref(err),
            parent,
            _b(name),
            ctypes.c_uint(int(total)),
            arr.ctypes.data_as(ctypes.POINTER(ctypes.c_int)),
            ctypes.c_uint(start),
            ctypes.c_uint(end if end is not None else K_CCMIO_END),
        )
        self._check(code, f"CCMIOWriteOpt1i({name})")

    def read_optstr(self, parent: CCMIOID, name: str) -> str:
        err = ctypes.c_int(K_CCMIO_NO_ERR)
        size = ctypes.c_int()
        code = self._CCMIOReadOptstr(
            ctypes.byref(err), parent, _b(name), ctypes.byref(size), None
        )
        if code == K_CCMIO_NO_NODE_ERR:
            raise KeyError(name)
        self._check(code, f"CCMIOReadOptstr({name})")
        buf = ctypes.create_string_buffer(size.value + 1)
        code = self._CCMIOReadOptstr(
            ctypes.byref(err), parent, _b(name), ctypes.byref(size), buf
        )
        self._check(code, f"CCMIOReadOptstr({name})")
        return buf.value.decode("utf-8", errors="replace")

    def read_opti(self, parent: CCMIOID, name: str) -> int:
        err = ctypes.c_int(K_CCMIO_NO_ERR)
        out = ctypes.c_int()
        code = self._CCMIOReadOpti(ctypes.byref(err), parent, _b(name), ctypes.byref(out))
        self._check(code, f"CCMIOReadOpti({name})")
        return out.value

    def read_opt1i(self, parent: CCMIOID, name: str, n: int) -> np.ndarray:
        """Read a 1-D int optional array (must contain exactly *n* values)."""
        out = np.empty(n, dtype=np.int32)
        err = ctypes.c_int(K_CCMIO_NO_ERR)
        code = self._CCMIOReadOpt1i(
            ctypes.byref(err),
            parent,
            _b(name),
            out.ctypes.data_as(ctypes.POINTER(ctypes.c_int)),
            K_CCMIO_START,
            K_CCMIO_END,
        )
        self._check(code, f"CCMIOReadOpt1i({name})")
        return out

    # -- mesh data ----------------------------------------------------------

    def write_map(self, map_id: CCMIOID, data: np.ndarray, max_id: int) -> None:
        arr = _i32(data)
        err = ctypes.c_int(K_CCMIO_NO_ERR)
        code = self._CCMIOWriteMap(
            ctypes.byref(err),
            map_id,
            ctypes.c_uint(arr.size),
            ctypes.c_uint(int(max_id)),
            arr.ctypes.data_as(ctypes.POINTER(ctypes.c_int)),
            K_CCMIO_START,
            K_CCMIO_END,
        )
        self._check(code, "CCMIOWriteMap")

    def read_map(self, map_id: CCMIOID, n: int) -> np.ndarray:
        out = np.empty(n, dtype=np.int32)
        err = ctypes.c_int(K_CCMIO_NO_ERR)
        code = self._CCMIOReadMap(
            ctypes.byref(err),
            map_id,
            out.ctypes.data_as(ctypes.POINTER(ctypes.c_int)),
            K_CCMIO_START,
            K_CCMIO_END,
        )
        self._check(code, "CCMIOReadMap")
        return out

    def write_vertices(
        self,
        node: CCMIOID,
        map_id: CCMIOID,
        coords: np.ndarray,
        scale: float = 0.001,
        start: int = 0,
        end: Optional[int] = None,
    ) -> None:
        """Write a chunk of vertex coordinates.

        *coords* is the flattened chunk ``[n_chunk][3]`` (only the chunk is
        passed); *start*/*end* are global vertex indices.
        """
        arr = _f32(coords)
        err = ctypes.c_int(K_CCMIO_NO_ERR)
        code = self._CCMIOWriteVerticesf(
            ctypes.byref(err),
            node,
            3,
            ctypes.c_float(scale),
            map_id,
            arr.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
            ctypes.c_uint(start),
            ctypes.c_uint(end if end is not None else K_CCMIO_END),
        )
        self._check(code, "CCMIOWriteVerticesf")

    def read_vertices(self, node: CCMIOID) -> tuple[int, float, CCMIOID, np.ndarray]:
        err = ctypes.c_int(K_CCMIO_NO_ERR)
        dims = ctypes.c_int()
        scale = ctypes.c_float()
        map_id = CCMIOID()
        n, _ = self.entity_size(node)
        out = np.empty(n * 3, dtype=np.float32)
        code = self._CCMIOReadVerticesf(
            ctypes.byref(err),
            node,
            ctypes.byref(dims),
            ctypes.byref(scale),
            ctypes.byref(map_id),
            out.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
            K_CCMIO_START,
            K_CCMIO_END,
        )
        self._check(code, "CCMIOReadVerticesf")
        return dims.value, scale.value, map_id, out.reshape(-1, 3)

    def write_cells(
        self,
        node: CCMIOID,
        map_id: CCMIOID,
        cell_types: np.ndarray,
        start: int = 0,
        end: Optional[int] = None,
    ) -> None:
        arr = _i32(cell_types)
        err = ctypes.c_int(K_CCMIO_NO_ERR)
        code = self._CCMIOWriteCells(
            ctypes.byref(err),
            node,
            map_id,
            arr.ctypes.data_as(ctypes.POINTER(ctypes.c_int)),
            ctypes.c_uint(start),
            ctypes.c_uint(end if end is not None else K_CCMIO_END),
        )
        self._check(code, "CCMIOWriteCells")

    def read_cells(self, node: CCMIOID) -> tuple[CCMIOID, np.ndarray]:
        err = ctypes.c_int(K_CCMIO_NO_ERR)
        map_id = CCMIOID()
        n, _ = self.entity_size(node)
        out = np.empty(n, dtype=np.int32)
        code = self._CCMIOReadCells(
            ctypes.byref(err),
            node,
            ctypes.byref(map_id),
            out.ctypes.data_as(ctypes.POINTER(ctypes.c_int)),
            K_CCMIO_START,
            K_CCMIO_END,
        )
        self._check(code, "CCMIOReadCells")
        return map_id, out

    def write_faces(
        self,
        node: CCMIOID,
        which: int,
        map_id: CCMIOID,
        stream_total: int,
        stream_chunk: np.ndarray,
        start: int,
        end: int,
    ) -> None:
        arr = _i32(stream_chunk)
        err = ctypes.c_int(K_CCMIO_NO_ERR)
        code = self._CCMIOWriteFaces(
            ctypes.byref(err),
            node,
            which,
            map_id,
            ctypes.c_uint(stream_total),
            arr.ctypes.data_as(ctypes.POINTER(ctypes.c_int)),
            ctypes.c_uint(start),
            ctypes.c_uint(end),
        )
        self._check(code, "CCMIOWriteFaces")

    def read_faces(self, node: CCMIOID, which: int) -> tuple[CCMIOID, np.ndarray]:
        err = ctypes.c_int(K_CCMIO_NO_ERR)
        map_id = CCMIOID()
        stream_size = ctypes.c_uint()
        code = self._CCMIOReadFaces(
            ctypes.byref(err),
            node,
            which,
            ctypes.byref(map_id),
            ctypes.byref(stream_size),
            None,
            K_CCMIO_START,
            K_CCMIO_END,
        )
        self._check(code, "CCMIOReadFaces")
        out = np.empty(int(stream_size.value), dtype=np.int32)
        code = self._CCMIOReadFaces(
            ctypes.byref(err),
            node,
            which,
            ctypes.byref(map_id),
            ctypes.byref(stream_size),
            out.ctypes.data_as(ctypes.POINTER(ctypes.c_int)),
            K_CCMIO_START,
            K_CCMIO_END,
        )
        self._check(code, "CCMIOReadFaces")
        return map_id, out

    def write_face_cells(
        self,
        node: CCMIOID,
        which: int,
        map_id: CCMIOID,
        cells: np.ndarray,
        start: int = 0,
        end: Optional[int] = None,
    ) -> None:
        arr = _i32(cells)
        err = ctypes.c_int(K_CCMIO_NO_ERR)
        code = self._CCMIOWriteFaceCells(
            ctypes.byref(err),
            node,
            which,
            map_id,
            arr.ctypes.data_as(ctypes.POINTER(ctypes.c_int)),
            ctypes.c_uint(start),
            ctypes.c_uint(end if end is not None else K_CCMIO_END),
        )
        self._check(code, "CCMIOWriteFaceCells")

    def read_face_cells(self, node: CCMIOID, which: int) -> np.ndarray:
        err = ctypes.c_int(K_CCMIO_NO_ERR)
        n, _ = self.entity_size(node)
        width = 2 if which == K_CCMIO_INTERNAL_FACES else 1
        out = np.empty(n * width, dtype=np.int32)
        code = self._CCMIOReadFaceCells(
            ctypes.byref(err),
            node,
            which,
            out.ctypes.data_as(ctypes.POINTER(ctypes.c_int)),
            K_CCMIO_START,
            K_CCMIO_END,
        )
        self._check(code, "CCMIOReadFaceCells")
        return out.reshape(-1, width)
