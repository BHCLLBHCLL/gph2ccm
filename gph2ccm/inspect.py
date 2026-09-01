"""Read back the ``gph2ccm.*`` descriptive metadata from a written ``.ccm``.

Roadmap item B1.  The converter writes physical intent (fields, solver
settings, MRF, periodic pairings, boundary-condition parameters) plus
capability/quality notes into the ``.ccm`` as namespaced opt nodes -- but
until now nothing in this repository could read them back, so the "what you
still have to add in STAR-CCM+" checklist in the README could only be
followed by hand.

The public libccmio API has **no generic child-node enumeration**, so this
module reads through the index nodes the writer emits (``*Names`` /
``*Keys``) plus the fixed note/quality names.

Reading is strictly read-only: :func:`read_metadata` opens the file with
``CCMIOOpenFile(..., kCCMIORead)``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from .ccmio import (
    CCMIO,
    K_CCMIO_BOUNDARY_REGION,
    K_CCMIO_PROBLEM_DESCRIPTION,
)

#: Fixed (non-indexed) metadata node names written by the converter.
NOTE_NODES = (
    "gph2ccm.Note.Processors",
    "gph2ccm.Note.MultiProcessor",
    "gph2ccm.Note.Dimension",
    "gph2ccm.Note.TwoDWrapping",
)
QUALITY_NODES = (
    "gph2ccm.Qual.Summary",
    "gph2ccm.Qual.Severity",
    "gph2ccm.Qual.Uncovered",
    "gph2ccm.Qual.Degenerate",
    "gph2ccm.Qual.Issues",
    "gph2ccm.Qual.Hints",
)

#: ``index node -> (value prefix, result key)`` for the indexed groups.
INDEXED_GROUPS = (
    ("gph2ccm.FieldNames", "gph2ccm.Field.", "fields"),
    ("gph2ccm.SolverKeys", "gph2ccm.Solver.", "solver_settings"),
    ("gph2ccm.MRFNames", "gph2ccm.MRF.", "mrf"),
    ("gph2ccm.PeriodicNames", "gph2ccm.Periodic.", "periodic"),
)

#: Human-readable split of the ``|``-encoded field / MRF / periodic values.
_FIELD_PARTS = ("location", "type", "units")
_MRF_PARTS = ("region", "type", "axis", "origin", "omega", "units")
_PERIODIC_PARTS = ("region", "shadow", "type", "axis", "angle")


def _try_optstr(ccmio: CCMIO, node: Any, name: str) -> Optional[str]:
    try:
        return ccmio.read_optstr(node, name)
    except KeyError:
        return None


def _split_encoded(value: str, parts: tuple[str, ...]) -> dict[str, str]:
    """Decode ``"a|b|c"`` into ``{"location": "a", ...}`` (missing -> "")."""
    chunks = value.split("|")
    out = {part: (chunks[i] if i < len(chunks) else "") for i, part in enumerate(parts)}
    return out


def read_metadata(path: str | Path, ccmio: Optional[CCMIO] = None) -> dict:
    """Return every ``gph2ccm.*`` node found in *path* as a nested dict.

    Keys: ``fields`` / ``solver_settings`` / ``mrf`` / ``periodic`` /
    ``boundary_conditions`` / ``notes`` / ``quality``.  Groups that are absent
    from the file are empty, so callers can iterate unconditionally.
    """
    ccmio = ccmio or CCMIO()
    meta: dict[str, Any] = {
        "fields": [],
        "solver_settings": {},
        "mrf": [],
        "periodic": [],
        "boundary_conditions": [],
        "notes": {},
        "quality": {},
        "file": str(path),
    }

    root = ccmio.open_file_readonly(str(path))
    try:
        _state, problem = ccmio.get_state(root)
        if problem is None:
            return meta

        for index_name, prefix, key in INDEXED_GROUPS:
            raw = _try_optstr(ccmio, problem, index_name)
            if not raw:
                continue
            names = [n for n in raw.split(",") if n]
            if key in ("fields", "mrf", "periodic"):
                parts = {
                    "fields": _FIELD_PARTS,
                    "mrf": _MRF_PARTS,
                    "periodic": _PERIODIC_PARTS,
                }[key]
                for name in names:
                    value = _try_optstr(ccmio, problem, f"{prefix}{name}") or ""
                    entry = {"name": name, "raw": value}
                    entry.update(_split_encoded(value, parts))
                    meta[key].append(entry)
            else:
                for name in names:
                    meta[key][name] = (
                        _try_optstr(ccmio, problem, f"{prefix}{name}") or ""
                    )

        for name in NOTE_NODES:
            value = _try_optstr(ccmio, problem, name)
            if value is not None:
                meta["notes"][name.rsplit(".", 1)[-1]] = value
        for name in QUALITY_NODES:
            value = _try_optstr(ccmio, problem, name)
            if value is not None:
                meta["quality"][name.rsplit(".", 1)[-1]] = value

        # Boundary regions: native Label/BoundaryType + descriptive BC params.
        for region in ccmio.iter_entities(problem, K_CCMIO_BOUNDARY_REGION):
            label = _try_optstr(ccmio, region, "Label") or ccmio.entity_name(region)
            entry = {
                "label": label,
                "type": _try_optstr(ccmio, region, "BoundaryType") or "",
                "params": {},
            }
            keys = _try_optstr(ccmio, region, "gph2ccm.BCKeys")
            for k in (keys.split(",") if keys else []):
                if k:
                    entry["params"][k] = (
                        _try_optstr(ccmio, region, f"gph2ccm.BC.{k}") or ""
                    )
            meta["boundary_conditions"].append(entry)
    finally:
        ccmio.close_file(root)
    return meta


# -- reporting -------------------------------------------------------------


def _fmt_table(rows: list[tuple[str, str]], indent: str = "    ") -> list[str]:
    if not rows:
        return []
    width = max(len(r[0]) for r in rows)
    return [f"{indent}{r[0].ljust(width)}  {r[1]}" for r in rows]


def format_report(meta: dict) -> str:
    """Render :func:`read_metadata` output as the STAR-CCM+ to-do checklist.

    The structure mirrors the "导入后须在 STAR-CCM+ 侧补充的清单" section of
    the README: each block states what the file carries and what the user
    still has to create by hand.
    """
    lines: list[str] = []
    add = lines.append

    add(f"gph2ccm inspect: {meta.get('file', '?')}")
    add("")

    # -- notes ------------------------------------------------------------
    notes = meta.get("notes") or {}
    if notes:
        add("能力 / 限制")
        lines.extend(_fmt_table([(k, v) for k, v in notes.items()]))
        add("")

    # -- quality ----------------------------------------------------------
    quality = meta.get("quality") or {}
    if quality:
        add("网格质量（只读诊断，导出器不修网格）")
        lines.extend(_fmt_table([(k, v) for k, v in quality.items()]))
        # Fix hints on their own lines -- they can be long.
        for k, v in quality.items():
            if k == "Hints" and v:
                for hint in v.split(" | "):
                    add(f"    -> {hint}")
        add("")

    # -- regions / BCs ----------------------------------------------------
    bcs = meta.get("boundary_conditions") or []
    if bcs:
        add(f"边界区域（{len(bcs)} 个）—— 需补数值")
        for entry in bcs:
            add(f"  - {entry['label']}  [{entry['type'] or '未指定'}]")
            for k, v in entry["params"].items():
                add(f"      {k} = {v}")
            if not entry["params"]:
                add("      （无描述性参数，需自行填写全部数值）")
        add("")

    # -- fields -----------------------------------------------------------
    fields = meta.get("fields") or []
    if fields:
        add(f"场变量（{len(fields)} 个，仅描述，无场数据）")
        lines.extend(
            _fmt_table(
                [
                    (
                        f["name"],
                        f"{f['location'] or 'cell'}"
                        f"/{f['type'] or 'scalar'}"
                        f"{' [' + f['units'] + ']' if f['units'] else ''}",
                    )
                    for f in fields
                ]
            )
        )
        add("")

    # -- solver -----------------------------------------------------------
    solver = meta.get("solver_settings") or {}
    if solver:
        add(f"求解设置（{len(solver)} 项，仅描述）")
        lines.extend(_fmt_table(list(solver.items())))
        add("")

    # -- MRF --------------------------------------------------------------
    mrf = meta.get("mrf") or []
    if mrf:
        add(f"MRF 旋转参考系（{len(mrf)} 个）—— 需在 STAR-CCM+ 手动建立")
        for f in mrf:
            add(
                f"  - {f['name']}: region={f['region'] or '?'} "
                f"type={f['type'] or 'rotating'} "
                f"omega={f['omega'] or '?'}{' ' + f['units'] if f['units'] else ''}"
            )
            add(f"      axis={f['axis'] or '?'}  origin={f['origin'] or '?'}")
        add("")

    # -- periodic ---------------------------------------------------------
    periodic = meta.get("periodic") or []
    if periodic:
        add(f"周期 / 滑移配对（{len(periodic)} 对）—— 需在 STAR-CCM+ 手动配对")
        for p in periodic:
            add(
                f"  - {p['name']}: {p['region'] or '?'} <-> "
                f"{p['shadow'] or '?'}  type={p['type'] or 'rotational'}"
            )
            add(f"      axis={p['axis'] or '?'}  angle={p['angle'] or '?'}")
        add("")

    if not any(
        [notes, quality, bcs, fields, solver, mrf, periodic]
    ):
        add("（该文件不含 gph2ccm.* 描述性元数据：未使用 --regions，或由旧版写出）")
        add("")

    add("以下必须在 STAR-CCM+ 中补充（转换器不会自动创建）：")
    for item in (
        "材料与物理模型（Continua → Physics）",
        "边界条件数值（入口/出口/壁面/湍流量）",
        "初始条件（CCM 不含结果场）",
        "MRF 旋转区域与参考坐标系",
        "周期 / 滑移 interface 配对",
        "2D 处理（薄方向设为 Symmetry/Empty）",
        "网格质量修复（未覆盖边界面 / 退化面）",
        "求解器离散格式与松弛因子",
        "并行分区（导出为单 processor）",
    ):
        add(f"  [ ] {item}")
    return "\n".join(lines)
