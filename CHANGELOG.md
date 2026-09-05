# Changelog

All notable changes to **gph2ccm** are documented here.
Format loosely follows [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Added

- **Transient solution support (E2)**: `SolutionField.phase` > 0 writes one
  `FieldPhase` entity per phase under the solution field set; `restart_info`
  (solver name / iteration / time / units / start angle) is written as the
  CCMIO restart node so STAR-CCM+ can label imported post data with
  iteration/time. Round-trip covered by `test_multiphase_and_restart`.
- **Macro applies boundary-condition values (E3)**: known numeric BC params
  (velocity magnitude, static/total pressure, static/total temperature,
  turbulence intensity, turbulent viscosity ratio, mass flow rate) are
  applied for real via
  `boundary.getValues().getCondition(<Profile>.class).setValue(n)`;
  profile classes javap-verified against the local STAR-CCM+ 2502 install
  (`starbase`/`flow`/`energy`/`turbulence.jar`). Unknown params and
  free-form `gph2ccm.Solver.*` metadata remain `println` reminders.
- **Field-write profiler (E5)**: `tools/profile_fields.py` isolates the
  marginal cost of solution-field writing (0/4/16 fields + raw disk
  reference). Verdict: GB/s throughput, no optimization needed.
- **`--fields` whitelist (F2)**: comma-separated case-insensitive solver
  field filter for FPH inputs (`--fields PRES,VEL`; vectors matched by base
  name), plus per-field write progress in the log.
- **FPH parse profiler (F1)**: `tools/profile_fph.py` splits
  `parse_gph_mesh` wall-clock into mesh decode vs LS_SPHFile field read.
  Measured on the 1.36 GB laptop sample: field read 1.8% / mesh decode
  98.2% -- field parsing is not the bottleneck.
- **Packaging (F3)**: `pyproject.toml` (PEP 621) with a `gph2ccm` console
  script and an `fph` extra (`pip install gph2ccm[fph]`); verified with a
  clean-venv `pip install .`.

## [0.2.0] — 2026-09-04

Milestone: from "mesh mover" to **mesh + metadata + periodic interfaces +
real solution-field exporter**. All features verified against STAR-CCM+
20.02.007-R8, including a 6.8M-cell real FPH end-to-end run.

### Added

- **Real solution-field writing (C2)**: `SolutionField` API (scalar `(n,)`
  / vector `(n,3)`) → CCM `FieldSet`/`FieldPhase`/`Field`/`FieldData`
  attached to the processor solution slot; vectors written as X/Y/Z
  component sub-fields (official `writeexample.cpp` pattern).
- **FPH result-file pipeline**: `.fph` input (mesh + results in one file)
  and `--fph` (separate result file); automatic scalar/vector grouping
  (`VELX/Y/Z` → `VEL`), solver-sentinel clearing (|v| > 1e20 → 0);
  requires optional `h5py`.
- **Effective periodic interfaces (C1)**: `regions["periodic"]` pairs are
  geometry-validated (translation point-match / rotation rigid congruence,
  fail-fast) then written as live `InterfaceDefinitions`
  (`ConditionType=PeriodicInterface`).
- **Descriptive metadata namespaces**: `gph2ccm.Field.* / Solver.* / MRF.* /
  Periodic.* / BC.* / Qual.* / Note.*` opt nodes + `*Names`/`*Keys` indexes.
- **`inspect` subcommand (B1)**: read back all `gph2ccm.*` nodes as a
  human-readable "after-import checklist".
- **Java macro generator (B2)**: `python -m gph2ccm macro out.ccm` emits a
  STAR-CCM+ journal that recreates MRF / periodic interfaces / boundary
  types from the metadata (verified on 2502 batch).
- **regions JSON schema validation (B3)**: invalid keys / over-long names
  fail before any output exists.
- **Quality diagnostics (B4)**: `gph2ccm.Qual.*` export-time summary +
  `diagnose_quality` API with error/warn/info levels.
- **Self-hosted CI workflow** (`self-hosted.yml`): full 27-case suite +
  1M-cell perf smoke on `[self-hosted, windows, starccm]`, manual
  `import-check` job; registration doc at `docs/self_hosted_ci.md`
  (runner v2.337.0 tested: `svc.cmd` removed, use `--runasservice`).
- **Performance baseline (D3)**: `tools/benchmark.py` + recorded baselines
  (synthetic 1M/3.3M blocks; real 6.8M-cell FPH end-to-end 2m41s,
  1330.5 MB output).
- **Version-behavior table (D2)**: 16 recorded dll/STAR-CCM+ quirks.
- **Manual verification checklist**: `docs/manual_verification.md` (M1–M7)
  with execution records.

### Changed

- `cell_centroids` now uses divergence-theorem volume weighting (exact on
  sliver cells vs 0.063 arithmetic-mean error).
- Interface virtual face-id `max_id` semantics confirmed against a native
  STAR-CCM+ export dump (kept, rationale in `conversion_issues_analysis.md`).
- Engineering hardening: `write()` try/finally cleanup (no leaked handles /
  partial files), fail-fast input validation everywhere, `--chunk-vertices`
  removed (was a no-op due to the dll 2-D chunking bug).
- Split-mode `verify` no longer false-positives on interface shared faces.

### Verified

- 31 regression cases (`python tests/test_writer.py`).
- Real 6.8M-cell FPH → CCM: region/cell/boundary counts match the source
  exactly; STAR-CCM+ batch import creates `Interface-1-2` automatically;
  all 10 solution fields round-trip with sentinels cleared.

## [0.1.0] — 2026-08

Initial working converter.

- GPH → legacy CCM: vertices, internal faces, boundary regions, cell types.
- Overlapping Cradle boundary de-duplication, `Default_Boundary_Region`.
- 2-D array chunk-write workaround (single-call vertices / face-cells)
  unblocking large-mesh import (`Reordering` hang root cause).
- `--split-fluid-regions` (multi fluid region + grid interfaces),
  `--cell-topology poly`, `--reorder rcm`, `--verify`, compression.
