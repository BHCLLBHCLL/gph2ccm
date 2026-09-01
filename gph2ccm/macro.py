"""Roadmap item B2 -- generate a STAR-CCM+ Java setup macro from metadata.

``python -m gph2ccm macro out.ccm`` reads the ``gph2ccm.*`` descriptive
metadata written by the converter (via :mod:`gph2ccm.inspect`) and emits a
Java macro that applies the recorded intent to the imported simulation:

* boundary types (``setBoundaryType(...)`` per boundary region);
* MRF rotating reference frames (``UserRotatingReferenceFrame`` + assignment
  through ``MotionSpecification``, following the official Simcenter STAR-CCM+
  2502 journal recipes);
* periodic / sliding pairings (direct interfaces between the two boundaries).

The macro is deliberately a **template**: numeric boundary-condition values
are emitted as comments/println reminders for the user to confirm, and every
generated statement is wrapped in its own ``try/catch`` so a missing region
or boundary never aborts the batch run.

API names were verified against the local STAR-CCM+ 20.02.007-R8 (2502)
installation:

* ``Boundary.setBoundaryType(Class<? extends BoundaryType>)`` -- starbase.jar;
* ``InletBoundary`` / ``PressureBoundary`` / ... -- ``star.common`` (the
  UserGuide's own example uses ``boundary.setBoundaryType(InletBoundary.class)``);
* ``ReferenceFrameManager.createReferenceFrame(Class, String)``,
  ``RotatingReferenceFrame.getRotationRate()`` / ``getAxis()`` / ``getOrigin()``
  -- motion.jar;
* ``Region.getValues().get(MotionSpecification.class)`` +
  ``setReferenceFrame(...)`` -- UserGuide p.1142;
* ``InterfaceManager.createDirectInterface(Boundary, Boundary)`` -- starbase.jar.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

#: Legacy CCM BoundaryType token -> star.common boundary class (STAR-CCM+ 2502).
#: Tokens not listed here need interface pairing or manual attention and are
#: emitted as comments instead of code.
BCTYPE_TO_JAVA: dict[str, str] = {
    "wall": "WallBoundary",
    "inlet": "InletBoundary",
    "intake": "InletBoundary",
    "inletvent": "InletBoundary",
    "outlet": "PressureBoundary",
    "pressure": "PressureBoundary",
    "exhaust": "PressureBoundary",
    "outletvent": "PressureBoundary",
    "stagnation": "StagnationBoundary",
    "mass": "MassFlowBoundary",
    "symmetry": "SymmetryBoundary",
    "free": "FreeStreamBoundary",
    "fan": "FanBoundary",
    "porous": "PorousBaffleBoundary",
    "radiator": "PorousBaffleBoundary",
}

_SKIP_TYPES = {"periodic", "cyclic", "slide", "interface", "couple", "blank",
               "dissolve"}


def _jstr(value: str) -> str:
    """Escape a Python string as a Java string literal."""
    out = (
        str(value)
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    return f'"{out}"'


def sanitize_class_name(name: str) -> str:
    """Turn *name* into a valid Java class identifier."""
    cleaned = re.sub(r"[^A-Za-z0-9_$]", "_", name)
    if not cleaned or cleaned[0].isdigit():
        cleaned = f"G_{cleaned}" if cleaned else "Gph2ccmSetup"
    return cleaned


def _fmt_vector(text: str, two_points: bool) -> Optional[str]:
    """Normalize an ``"x, y, z"`` (or ``"x y z"``) metadata vector to a
    STAR-CCM+ ``setDefinition`` literal.  Returns ``None`` if unparsable."""
    if not text:
        return None
    parts = [p for p in re.split(r"[,\s;]+", str(text).strip()) if p]
    if len(parts) != 3:
        return None
    try:
        nums = [float(p) for p in parts]
    except ValueError:
        return None
    if two_points:
        # Axis definition: two points (origin + origin + direction unit offset).
        return (
            "[[%g, %g, %g], [%g, %g, %g]]"
            % (nums[0], nums[1], nums[2], nums[0], nums[1], nums[2] + 1.0)
        )
    return "[%g, %g, %g]" % (nums[0], nums[1], nums[2])


def _wrap(stmts: list[str], err_msg: str) -> list[str]:
    """Wrap statements in a per-block try/catch so one failure never aborts."""
    out = ["try {"]
    out.extend(f"    {s}" for s in stmts)
    out.append(
        f"}} catch (Exception e) {{ simulation_0.println({_jstr(err_msg + ': ')} + e.getMessage()); }}"
    )
    return out


def generate_macro(
    meta: dict,
    ccm_path: Optional[str] = None,
    class_name: str = "Gph2ccmSetup",
) -> str:
    """Return a complete Java macro source applying *meta* in STAR-CCM+.

    Parameters
    ----------
    meta:
        Metadata dict as returned by :func:`gph2ccm.inspect.read_metadata`.
    ccm_path:
        Optional path to the ``.ccm`` mesh.  When given, the macro imports the
        volume mesh itself if the active simulation has no regions yet (so the
        macro also works in a fresh ``starccm+ -new -batch`` run).
    class_name:
        Java class name for the generated macro.
    """
    cls = sanitize_class_name(class_name)
    L: list[str] = []
    add = L.append

    add("// ---------------------------------------------------------------------------")
    add("// " + cls + ".java -- generated by gph2ccm macro (B2)")
    add(f"// source metadata: {meta.get('file', '?')}")
    add("//")
    add("// 用法（二选一）：")
    add("//   1) GUI：打开导入网格后的 .sim，Tools > Macros > Play Macro 选择本文件")
    add("//   2) 批处理：starccm+ -batch " + cls + ".java model.sim")
    add("//      或全新会话（自动导入体网格）：starccm+ -new -batch " + cls + ".java")
    add("//")
    add("// 半自动模板：边界类型 / MRF / 周期配对自动创建；边界数值仍需人工确认。")
    add("// 每段语句独立 try/catch：缺少对应 region/boundary 时打印告警并继续。")
    add("// ---------------------------------------------------------------------------")
    add("")
    add("import star.common.*;")
    add("import star.base.neo.*;")
    add("import star.motion.*;")
    add("")
    add(f"public class {cls} extends StarMacro {{")
    add("")
    add("  @Override")
    add("  public void execute() {")
    add("    Simulation simulation_0 = getActiveSimulation();")
    add('    simulation_0.println("gph2ccm setup macro: start");')
    add("")

    # -- optional mesh import -------------------------------------------------
    if ccm_path:
        add(f"    // 自动导入体网格（若当前 simulation 已有 region 则跳过）")
        add("    if (simulation_0.getRegionManager().getRegions().isEmpty()) {")
        add("      try {")
        add(
            "        simulation_0.get(ImportManager.class)"
            f".importMeshFiles({_jstr(str(Path(ccm_path).resolve()))});"
        )
        add(
            '        simulation_0.println("gph2ccm: volume mesh imported from '
            + _jstr(ccm_path)[1:-1]
            + '");'
        )
        add(
            '      } catch (Exception e) { simulation_0.println("gph2ccm: mesh '
            'import failed: " + e.getMessage()); }'
        )
        add("    }")
        add("")

    # -- boundary types -------------------------------------------------------
    bcs = meta.get("boundary_conditions") or []
    typed = [b for b in bcs if (b.get("type") or "") in BCTYPE_TO_JAVA]
    if typed:
        add("    // ---- 边界类型 ----")
        for b in typed:
            label = b["label"]
            jcls = BCTYPE_TO_JAVA[b["type"]]
            stmts = [
                "Boundary b = findBoundary(simulation_0, "
                f"{_jstr(label)});",
                f"b.setBoundaryType({jcls}.class);",
                f'simulation_0.println("gph2ccm: boundary {_jstr(label)[1:-1]} -> {jcls}");',
            ]
            L.extend(_wrap(stmts, f"gph2ccm: boundary '{label}' not found/skip"))
            add("")
        note = [b["label"] for b in bcs if (b.get("type") or "") in _SKIP_TYPES]
        if note:
            add("    // 以下区域需要 interface 配对或人工处理，未自动改类型：")
            for label in note:
                add(f"    //   - {label}")
            add("")

    # -- boundary condition parameters (comments / reminders) -----------------
    paramed = [b for b in bcs if b.get("params")]
    if paramed:
        add("    // ---- 边界条件数值（仅提醒，需人工确认） ----")
        for b in paramed:
            for k, v in b["params"].items():
                add(
                    f'    simulation_0.println("gph2ccm TODO: boundary '
                    f'{_jstr(b["label"])[1:-1]}: {k} = {v}");'
                )
        add("")

    # -- MRF ------------------------------------------------------------------
    mrf = meta.get("mrf") or []
    for idx, m in enumerate(mrf):
        name = m.get("name") or f"mrf{idx + 1}"
        region = m.get("region") or ""
        omega = m.get("omega") or ""
        units = m.get("units") or ""
        axis = _fmt_vector(m.get("axis"), two_points=True)
        origin = _fmt_vector(m.get("origin"), two_points=False)
        frame_var = f"userRotatingReferenceFrame_{idx}"
        add(f"    // ---- MRF: {name} (region={region}, omega={omega} {units}) ----")
        stmts = [
            f"UserRotatingReferenceFrame {frame_var} = "
            "((ReferenceFrameManager) simulation_0.getReferenceFrameManager())"
            f".createReferenceFrame(UserRotatingReferenceFrame.class, "
            f"{_jstr('gph2ccm MRF ' + str(idx + 1))});",
        ]
        if omega:
            unit_note = f"  // metadata units: {units}" if units else ""
            stmts.append(
                f"{frame_var}.getRotationRate().setValue({omega});"
                f"{unit_note}"
            )
        if axis:
            stmts.append(f"{frame_var}.getAxis().setDefinition({_jstr(axis)});")
        if origin:
            stmts.append(f"{frame_var}.getOrigin().setDefinition({_jstr(origin)});")
        if region:
            stmts.extend(
                [
                    "Region mrfRegion = simulation_0.getRegionManager()"
                    f".getRegion({_jstr(region)});",
                    "MotionSpecification motionSpecification = mrfRegion.getValues()"
                    ".get(MotionSpecification.class);",
                    f"motionSpecification.setReferenceFrame({frame_var});",
                ]
            )
        stmts.append(f'simulation_0.println("gph2ccm: MRF {name} applied");')
        L.extend(_wrap(stmts, f"gph2ccm: MRF '{name}' failed (region '{region}' missing?)"))
        add("")

    # -- periodic / sliding pairings ------------------------------------------
    periodic = meta.get("periodic") or []
    for idx, p in enumerate(periodic):
        name = p.get("name") or f"periodic{idx + 1}"
        b1 = p.get("region") or ""
        b2 = p.get("shadow") or ""
        ptype = p.get("type") or "rotational"
        add(f"    // ---- 周期/滑移配对: {name} ({b1} <-> {b2}, {ptype}) ----")
        stmts = [
            "Boundary p0 = findBoundary(simulation_0, "
            f"{_jstr(b1)});",
            "Boundary p1 = findBoundary(simulation_0, "
            f"{_jstr(b2)});",
            "simulation_0.getInterfaceManager()"
            f".createDirectInterface(p0, p1);",
            f'simulation_0.println("gph2ccm: interface {name} created ({b1} <-> {b2}); '
            f'若为周期型请在 Interfaces 节点改为 {ptype} 类型");',
        ]
        L.extend(_wrap(stmts, f"gph2ccm: periodic pairing '{name}' failed"))
        add("")

    add('    simulation_0.println("gph2ccm setup macro: done");')
    add("  }")
    add("")
    add("  // Boundary names are unique across the whole mesh in gph2ccm output,")
    add("  // but the API looks them up per region, so scan every region.")
    add("  private static Boundary findBoundary(Simulation sim, String name) {")
    add("    for (Region r : sim.getRegionManager().getRegions()) {")
    add("      try {")
    add("        Boundary b = r.getBoundaryManager().getBoundary(name);")
    add("        if (b != null) return b;")
    add("      } catch (Exception ignored) { }")
    add("    }")
    add("    throw new IllegalArgumentException(\"boundary not found: \" + name);")
    add("  }")
    add("}")
    return "\n".join(L) + "\n"


_UNSET = object()


def generate_macro_for_file(
    path: str | Path,
    ccm_path=_UNSET,
    **kwargs,
) -> str:
    """Read metadata from *path* and return the macro source.

    ``ccm_path`` defaults to *path* itself (so the macro can auto-import the
    mesh); pass ``ccm_path=None`` to omit the import block.
    """
    from .inspect import read_metadata

    if ccm_path is _UNSET:
        ccm_path = str(path)
    return generate_macro(
        read_metadata(path), ccm_path=ccm_path,
        **kwargs,
    )
