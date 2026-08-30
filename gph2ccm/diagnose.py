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
        ``n_uncovered_boundary``, ``n_degenerate_boundary``, ``issues`` (list
        of human-readable strings), ``ok`` (bool).
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

    issues: list[str] = []
    if metrics["n_uncovered_boundary"]:
        issues.append(
            f"{metrics['n_uncovered_boundary']} boundary faces not assigned to "
            "any region (Default_Boundary_Region)"
        )
    if n_degenerate:
        issues.append(f"{n_degenerate} degenerate boundary faces (npe < 3)")
    metrics["issues"] = issues
    metrics["ok"] = not issues
    return metrics
