"""Mesh-quality diagnostics for gph2ccm (read-only).

gph2ccm never modifies the mesh -- the "keep boundary" scope decision keeps it
a mesh+description exporter, not a repair tool.  This module therefore only
*reports* potential quality issues.

Heavy topological checks (duplicate faces, cell closure, repeated vertices)
live in ``tools/topo_check.py`` and run against an already-written ``.ccm``.
Here we surface the cheap, export-time metrics that don't require a full
re-read of the file, so the converter can flag the obvious problems (faces
left in ``Default_Boundary_Region``, degenerate boundary faces) right away.
"""

from __future__ import annotations

import numpy as np

from .model import CcmModel

#: Severity levels, most severe first.
SEVERITIES = ("error", "warning", "info")


def diagnose_quality(model: CcmModel, ld: dict) -> dict:
    """Return cheap mesh-quality metrics without modifying the mesh.

    Parameters
    ----------
    model:
        The assembled :class:`~gph2ccm.model.CcmModel`.
    ld:
        The GPH ``link_data`` dict (provides per-face node counts).

    Returns
    -------
    dict
        Keys: ``n_vertices``, ``n_cells``, ``n_internal_faces``,
        ``n_boundary_faces``, ``n_boundary_regions``,
        ``n_uncovered_boundary``, ``n_degenerate_boundary``,
        ``findings`` (graded list of ``{"severity", "message", "hint"}``
        dicts -- B4), ``issues`` (backwards-compatible plain-message list),
        ``has_errors`` (bool: any ``error``-severity finding), ``ok`` (bool).
    """
    npe = np.asarray(ld.get("npe"), dtype=np.int64) if ld else np.empty(0, np.int64)
    boundary_face_ids = (
        np.concatenate([r.face_ids for r in model.boundary_regions])
        if model.boundary_regions
        else np.empty(0, np.int64)
    )

    n_degenerate = 0
    if boundary_face_ids.size and npe.size:
        n_degenerate = int((npe[boundary_face_ids] < 3).sum())

    metrics: dict = {
        "n_vertices": int(model.vertices.shape[0]),
        "n_cells": model.n_cells,
        "n_internal_faces": int(model.internal_face_ids.size),
        "n_boundary_faces": int(boundary_face_ids.size),
        "n_boundary_regions": len(model.boundary_regions),
        "n_uncovered_boundary": int(model.default_face_ids.size),
        "n_degenerate_boundary": n_degenerate,
    }

    # Graded findings (B4): severity + fix hint.  The hints point at the
    # STAR-CCM+ / tooling side because gph2ccm itself never repairs meshes.
    findings: list[dict] = []
    if metrics["n_uncovered_boundary"]:
        findings.append(
            {
                "severity": "warning",
                "message": (
                    f"{metrics['n_uncovered_boundary']} boundary faces not assigned to "
                    "any region (Default_Boundary_Region)"
                ),
                "hint": (
                    "这些面会进入 Default_Boundary_Region，仍可导入；"
                    "在 STAR-CCM+ 中为其指定边界类型（wall 等），"
                    "或检查上游 LS_SurfaceRegions 是否漏配"
                ),
            }
        )
    if n_degenerate:
        findings.append(
            {
                "severity": "error",
                "message": f"{n_degenerate} degenerate boundary faces (npe < 3)",
                "hint": (
                    "退化面会阻断求解：用 tools/topo_check.py 定位具体面，"
                    "在 STAR-CCM+ 的 Surface Repair 中修复后重新导出"
                ),
            }
        )

    metrics["findings"] = findings
    # ``issues`` keeps its pre-B4 shape (plain strings) for compatibility.
    metrics["issues"] = [f["message"] for f in findings]
    metrics["has_errors"] = any(f["severity"] == "error" for f in findings)
    metrics["ok"] = not findings
    return metrics


def format_findings(diag: dict) -> list[str]:
    """Render :func:`diagnose_quality` findings as graded, hint-carrying lines.

    Output shape (one finding = two lines)::

        [ERROR]   2 degenerate boundary faces (npe < 3)
                  -> 退化面会阻断求解：...
    """
    lines: list[str] = []
    for finding in diag.get("findings", []):
        severity = finding.get("severity", "info").upper()
        lines.append(f"[{severity}] {finding['message']}")
        hint = finding.get("hint")
        if hint:
            lines.append(f"          -> {hint}")
    return lines
