# AGENTS.md

## Project Overview

**gph2ccm** converts Software Cradle GPH meshes to legacy STAR-CCM+ `.ccm`
files:

1. `gphdecoding` (`../gphdecoding`, `gph2cgns.parse_gph_mesh`) — GPH binary parse
2. `gph2ccm.model` — build the CCM mesh model (cells, faces, boundary regions)
3. `gph2ccm.ccmio` — ctypes bindings to `ccmio.dll` / `libccmio`
4. `gph2ccm.convert` — orchestration / writer

Package import name: `gph2ccm`. CLI: `python -m gph2ccm`.

## Agent instructions

- Do **not** reimplement GPH binary parsing; use `gphdecoding` through
  `gph2ccm.deps` (env override `GPH2CCM_GPHDECODING`).
- Do **not** reimplement the CCM binary format; drive `ccmio.dll` / `libccmio`
  through `gph2ccm.ccmio` (env override `GPH2CCM_CCMIO_DLL`).
- Boundary faces must appear in exactly one boundary region: Cradle exports
  overlapping `LS_SurfaceRegions` (`open` vs `@PartSurface_*`); prefer
  non-`@PartSurface_` names and de-duplicate in file order.
- `CCMIOWriteOpt1i`/`CCMIOWriteCells` take the **total** array length plus a
  chunk pointer; `CCMIOWriteFaces` takes the total stream length. Chunked
  writes must pass global start/end indices.
- 2-D arrays (vertex coordinates `[3][n]`, internal face-cells `[2][n]`)
  must be written in a **single** `CCMIOWrite*` call: the bundled
  `ccmio.dll` misplaces chunked 2-D writes (start/end treated as flat
  offsets), which corrupts cell connectivity and hangs STAR-CCM+ import.
- `CCMIOID` is `{CCMIONode root; CCMIONode node; int id; int type; int version;}`
  (ctypes layout in `ccmio.py`).
- Large samples live under `tests/` and are gitignored.

## Quick checks

```bash
python -m gph2ccm --help
python -c "from gph2ccm.deps import find_gphdecoding_root; print(find_gphdecoding_root())"
python -c "from gph2ccm.ccmio import find_ccmio_library; print(find_ccmio_library())"
python tests/test_writer.py
```
