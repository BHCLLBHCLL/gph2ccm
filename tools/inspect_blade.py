"""Debug: read InterfaceDefinitions strings from a freshly written split file."""
import ctypes
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, ".")

import numpy as np

from gph2ccm.ccmio import CCMIO, CCMIONode
from gph2ccm.convert import CcmMeshWriter
from gph2ccm.model import build_model

# reuse the synthetic mesh builder from the test module
sys.path.insert(0, "tests")
import test_writer as tw  # noqa: E402


def main() -> None:
    mesh = tw.make_split_gph()
    model = build_model(mesh, tw.SPLIT_REGIONS, split_regions=True)
    ccmio = CCMIO()
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "split.ccm"
        writer = CcmMeshWriter(ccmio, out, verbose=False, split_regions=True)
        writer.write(model, mesh["link_data"])
        ccmio.compress(out)

        lib = ccmio._lib
        lib.CCMIOGetNode.restype = ctypes.c_int
        lib.CCMIOGetNode.argtypes = [
            ctypes.POINTER(ctypes.c_int), CCMIONode, ctypes.c_char_p,
            ctypes.POINTER(CCMIONode),
        ]
        lib.CCMIOReadNodestr.restype = ctypes.c_int
        lib.CCMIOReadNodestr.argtypes = [
            ctypes.POINTER(ctypes.c_int), CCMIONode, ctypes.c_char_p,
            ctypes.c_char_p, ctypes.POINTER(ctypes.c_int),
        ]
        lib.CCMIOReadNodei.restype = ctypes.c_int
        lib.CCMIOReadNodei.argtypes = [
            ctypes.POINTER(ctypes.c_int), CCMIONode, ctypes.c_char_p,
            ctypes.POINTER(ctypes.c_int),
        ]

        def get(parent, name):
            err = ctypes.c_int(0)
            node = CCMIONode()
            code = lib.CCMIOGetNode(
                ctypes.byref(err), parent, name.encode(), ctypes.byref(node)
            )
            return code, node

        def rstr(node, name):
            err = ctypes.c_int(0)
            buf = ctypes.create_string_buffer(256)
            sz = ctypes.c_int(len(buf) - 1)
            code = lib.CCMIOReadNodestr(
                ctypes.byref(err), node, name.encode(), buf, ctypes.byref(sz)
            )
            return code, sz.value, buf.value

        def rint(node, name):
            err = ctypes.c_int(0)
            val = ctypes.c_int(0)
            code = lib.CCMIOReadNodei(
                ctypes.byref(err), node, name.encode(), ctypes.byref(val)
            )
            return code, val.value

        root = ccmio.open_file_readonly(str(out))
        code, idf = get(root.root, "InterfaceDefinitions")
        print("InterfaceDefinitions code:", code)
        code, inode = get(idf, "Interface-0")
        print("Interface-0 code:", code)
        for nm in ("Name", "Configuration", "ConditionType", "Boundary0", "Boundary1"):
            if nm in ("Boundary0", "Boundary1"):
                c, v = rint(inode, nm)
                print(f"  {nm}: code={c} val={v}")
            else:
                c, s, b = rstr(inode, nm)
                print(f"  {nm}: code={c} size={s} raw={b!r}")
        ccmio.close_file(root)


if __name__ == "__main__":
    main()
