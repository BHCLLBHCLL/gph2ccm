"""Command line entry point for gph2ccm."""

from __future__ import annotations

import argparse
import sys

from .convert import DEFAULT_CHUNK_FACES, DEFAULT_CHUNK_VERTICES, convert_gph


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m gph2ccm",
        description="Convert Software Cradle GPH meshes to STAR-CCM+ CCM files.",
    )
    parser.add_argument("gph", help="input Cradle mesh file (*.gph)")
    parser.add_argument(
        "output",
        nargs="?",
        help="output .ccm file (default: <gph stem>.ccm next to the input)",
    )
    parser.add_argument(
        "--regions",
        metavar="JSON",
        help="optional CHT regions JSON (fluid_regions/solid_regions, "
        "boundary_types, ...)",
    )
    parser.add_argument(
        "--boundary-types",
        metavar="JSON",
        help='optional JSON mapping boundary region names to CCM BoundaryType '
        'strings, e.g. {"inlet_1": "inlet"}',
    )
    parser.add_argument(
        "--ccmio-dll",
        metavar="PATH",
        help="path to ccmio.dll / libccmio.so (default: auto-discover, "
        "including the STAR-CCM+ installation)",
    )
    parser.add_argument(
        "--no-compress",
        action="store_true",
        help="skip the final CCMIOCompress pass",
    )
    parser.add_argument(
        "--backup",
        action="store_true",
        help="keep an existing output as <output>.bak instead of deleting it",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="read the generated file back and sanity-check its topology",
    )
    parser.add_argument(
        "--force-material",
        choices=("fluid", "solid", "none"),
        default=None,
        help="override every cell-table MaterialType (default: derive from "
        "regions JSON / name heuristics). 'none' keeps the derived value.",
    )
    parser.add_argument(
        "--cell-topology",
        choices=("none", "poly", "auto"),
        default="poly",
        help="write an explicit PROSTAR CellTopologyType for every cell "
        "(default: poly=255, matching Cradle cut-cell meshes and avoiding "
        "slow per-cell shape detection during STAR-CCM+ import). "
        "'none' omits the field entirely.",
    )
    parser.add_argument(
        "--title",
        default=None,
        help="file title stored in the CCM header (default: gph stem)",
    )
    parser.add_argument(
        "--chunk-vertices",
        type=int,
        default=DEFAULT_CHUNK_VERTICES,
        help=f"vertices written per CCMIO call (default {DEFAULT_CHUNK_VERTICES})",
    )
    parser.add_argument(
        "--chunk-faces",
        type=int,
        default=DEFAULT_CHUNK_FACES,
        help=f"faces written per CCMIO call (default {DEFAULT_CHUNK_FACES})",
    )
    parser.add_argument("-q", "--quiet", action="store_true", help="suppress progress")
    args = parser.parse_args(argv)

    try:
        convert_gph(
            args.gph,
            args.output,
            regions_json=args.regions,
            boundary_types_json=args.boundary_types,
            ccmio_dll=args.ccmio_dll,
            compress=not args.no_compress,
            backup=args.backup,
            title=args.title,
            chunk_vertices=args.chunk_vertices,
            chunk_faces=args.chunk_faces,
            verify=args.verify,
            force_material=(
                None if args.force_material in (None, "none") else args.force_material
            ),
            cell_topology=(
                None if args.cell_topology == "none" else args.cell_topology
            ),
            verbose=not args.quiet,
        )
    except Exception as exc:  # keep CLI output tidy
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
