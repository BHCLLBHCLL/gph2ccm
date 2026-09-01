"""Schema validation for the optional ``regions`` JSON (roadmap item B3).

The regions JSON is the single input through which users carry physical
intent into the ``.ccm`` file (fluid/solid regions, structured boundary
conditions, fields, solver settings, MRF, periodic pairings).  Every key is
*optional*, and a wrong type or a misspelled key was previously ignored
silently -- the conversion ran to completion and the metadata simply never
made it into the file.

This module validates the JSON **before** conversion and reports every
problem at once, with a dotted path and (when the raw text is available) a
line number, so the user fixes the file in one pass instead of debugging a
produced ``.ccm`` afterwards.

The validator is intentionally dependency-free (no jsonschema) and only
describes what :mod:`gph2ccm.model` / :mod:`gph2ccm.convert` actually
consume -- it is not a guess at a general schema.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

# libccmio rejects opt-node names longer than K_CCMIO_MAX_STRING_LENGTH
# (32 chars) with kCCMIOBadParameterErr; the "gph2ccm." namespace eats 8 of
# them, and the group prefix ("Field." etc.) eats more.
MAX_NODE_NAME = 32

#: Top-level keys understood by gph2ccm.
KNOWN_TOP_LEVEL = frozenset(
    {
        "fluid_regions",
        "solid_regions",
        "boundary_types",
        "boundary_conditions",
        "fields",
        "solver_settings",
        "mrf",
        "periodic",
    }
)

#: Keys of each ``fields[]`` entry (all optional but ``name``).
FIELD_KEYS = frozenset({"name", "location", "type", "units"})
#: Keys of each ``mrf[]`` entry.
MRF_KEYS = frozenset(
    {"name", "region", "type", "axis", "origin", "omega", "units"}
)
#: Keys of each ``periodic[]`` entry.
PERIODIC_KEYS = frozenset(
    {"name", "region", "shadow", "type", "axis", "angle", "translation"}
)
#: Keys of each ``boundary_conditions[<region>]`` entry.
BC_KEYS = frozenset({"type", "params"})


class RegionsError(ValueError):
    """Raised when the regions JSON does not match the expected schema."""


# -- raw-text key index (for line numbers) --------------------------------


def _key_line_index(text: str) -> dict[str, int]:
    """Map a dotted JSON path (``mrf[0].omega``) to its line number.

    A small scanner rather than a full parser: it walks the text tracking
    object/array nesting and records the line of every object key.  It is
    only used to decorate error messages, so if a path cannot be located the
    validator still reports the problem without a line number.
    """
    index: dict[str, int] = {}
    # frames: {"kind": "obj"|"arr", "path": str, "idx": int (arr only)}
    stack: list[dict] = []
    pending_key: Optional[str] = None
    i = 0
    n = len(text)
    line = 1

    def value_path() -> str:
        """Path that the value about to be parsed will occupy."""
        if not stack:
            return ""
        top = stack[-1]
        if top["kind"] == "arr":
            if top["idx"] == -1:
                top["idx"] = 0
            return f"{top['path']}[{top['idx']}]"
        return pending_key or top["path"]

    def child_path(key: str) -> str:
        top = stack[-1]
        return f"{top['path']}.{key}" if top["path"] else key

    while i < n:
        c = text[i]
        if c == "\n":
            line += 1
            i += 1
            continue
        if c in " \t\r":
            i += 1
            continue
        if c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                if text[j] == "\\":
                    j += 1
                j += 1
            token = text[i + 1 : j]
            i = j + 1
            k = i
            while k < n and text[k] in " \t\r\n":
                if text[k] == "\n":
                    line += 1
                k += 1
            is_key = (
                k < n and text[k] == ":" and bool(stack) and stack[-1]["kind"] == "obj"
            )
            if is_key:
                pending_key = child_path(token)
                index.setdefault(pending_key, line)
            else:
                # A string *value*: occupy the enclosing array slot so the
                # following "," bumps the index.
                value_path()
            continue
        if c == "{":
            stack.append({"kind": "obj", "path": value_path()})
            pending_key = None
            i += 1
            continue
        if c == "[":
            stack.append({"kind": "arr", "path": value_path(), "idx": -1})
            pending_key = None
            i += 1
            continue
        if c in "}]":
            stack.pop()
            pending_key = None
            i += 1
            continue
        if c == ",":
            if stack and stack[-1]["kind"] == "arr":
                stack[-1]["idx"] += 1
            i += 1
            continue
        # Any other character starts a scalar value (number / true / false /
        # null).  Occupy the enclosing array slot.
        value_path()
        i += 1
    return index


# -- validation ------------------------------------------------------------


class _Validator:
    def __init__(self, text: Optional[str] = None) -> None:
        self.lines = _key_line_index(text) if text else {}
        self.errors: list[str] = []

    def fail(self, path: str, msg: str) -> None:
        line = self.lines.get(path)
        prefix = f"line {line}: " if line else ""
        self.errors.append(f"{prefix}{path or '<root>'}: {msg}")

    # -- helpers ---------------------------------------------------------
    def _type_name(self, value: Any) -> str:
        return type(value).__name__

    def _check_str_list(self, path: str, value: Any) -> None:
        if not isinstance(value, list):
            self.fail(path, f"expected a list of strings, got {self._type_name(value)}")
            return
        for i, item in enumerate(value):
            if not isinstance(item, str):
                self.fail(
                    f"{path}[{i}]",
                    f"expected string, got {self._type_name(item)}",
                )

    def _check_name_length(self, path: str, name: Any, prefix: str) -> None:
        """CCM opt-node names are capped at :data:`MAX_NODE_NAME` chars."""
        if not isinstance(name, str) or not name:
            return
        node = f"gph2ccm.{prefix}.{name}"
        if len(node) > MAX_NODE_NAME:
            self.fail(
                path,
                f"name too long: '{node}' is {len(node)} chars, "
                f"CCM opt-node names are limited to {MAX_NODE_NAME} "
                f"(shorten the name by {len(node) - MAX_NODE_NAME} chars)",
            )

    def _check_keys(
        self, path: str, obj: dict, allowed: frozenset, label: str
    ) -> None:
        for key in obj:
            if not isinstance(key, str):
                self.fail(path, f"{label} key must be a string, got {key!r}")
                continue
            if key not in allowed:
                self.fail(
                    f"{path}.{key}",
                    f"unknown key (allowed: {', '.join(sorted(allowed))})",
                )

    def _check_entry(
        self,
        path: str,
        entry: Any,
        allowed: frozenset,
        label: str,
        name_prefix: Optional[str] = None,
        scalar_keys: frozenset = frozenset(),
    ) -> None:
        if not isinstance(entry, dict):
            self.fail(path, f"expected an object, got {self._type_name(entry)}")
            return
        self._check_keys(path, entry, allowed, label)
        name = entry.get("name")
        if name is not None and not isinstance(name, str):
            self.fail(f"{path}.name", f"expected string, got {self._type_name(name)}")
        elif not name:
            self.fail(f"{path}.name", "required (entries without a name are skipped)")
        elif name_prefix:
            self._check_name_length(f"{path}.name", name, name_prefix)
        for key in scalar_keys:
            if key in entry and not isinstance(entry[key], (str, int, float, bool)):
                self.fail(
                    f"{path}.{key}",
                    f"expected a scalar, got {self._type_name(entry[key])}",
                )

    # -- top level -------------------------------------------------------
    def validate(self, regions: Any, path: str = "") -> None:
        if not isinstance(regions, dict):
            self.fail(path, f"expected a JSON object, got {self._type_name(regions)}")
            return

        for key in regions:
            if not isinstance(key, str):
                self.fail(path, f"top-level key must be a string, got {key!r}")
            elif key not in KNOWN_TOP_LEVEL:
                self.fail(
                    f"{key}",
                    "unknown top-level key "
                    f"(allowed: {', '.join(sorted(KNOWN_TOP_LEVEL))})",
                )

        self._check_str_list("fluid_regions", regions.get("fluid_regions", []))
        self._check_str_list("solid_regions", regions.get("solid_regions", []))

        bt = regions.get("boundary_types")
        if bt is not None:
            if not isinstance(bt, dict):
                self.fail(
                    "boundary_types",
                    f"expected an object, got {self._type_name(bt)}",
                )
            else:
                for k, v in bt.items():
                    if not isinstance(v, str):
                        self.fail(
                            f"boundary_types.{k}",
                            f"expected string, got {self._type_name(v)}",
                        )

        bcs = regions.get("boundary_conditions")
        if bcs is not None:
            if not isinstance(bcs, dict):
                self.fail(
                    "boundary_conditions",
                    f"expected an object, got {self._type_name(bcs)}",
                )
            else:
                for region, spec in bcs.items():
                    rpath = f"boundary_conditions.{region}"
                    if not isinstance(spec, dict):
                        self.fail(
                            rpath,
                            f"expected an object, got {self._type_name(spec)}",
                        )
                        continue
                    self._check_keys(rpath, spec, BC_KEYS, "boundary condition")
                    if "type" in spec and not isinstance(spec["type"], str):
                        self.fail(
                            f"{rpath}.type",
                            f"expected string, got {self._type_name(spec['type'])}",
                        )
                    params = spec.get("params")
                    if params is not None and not isinstance(params, dict):
                        self.fail(
                            f"{rpath}.params",
                            f"expected an object, got {self._type_name(params)}",
                        )
                    elif isinstance(params, dict):
                        for pk, pv in params.items():
                            if not isinstance(pv, (str, int, float, bool)):
                                self.fail(
                                    f"{rpath}.params.{pk}",
                                    f"expected a scalar, got "
                                    f"{self._type_name(pv)}",
                                )
                            node = f"gph2ccm.BC.{pk}"
                            if len(node) > MAX_NODE_NAME:
                                self.fail(
                                    f"{rpath}.params.{pk}",
                                    f"param name too long: '{node}' is "
                                    f"{len(node)} chars, limit {MAX_NODE_NAME}",
                                )

        fields = regions.get("fields")
        if fields is not None:
            if not isinstance(fields, list):
                self.fail(
                    "fields", f"expected a list, got {self._type_name(fields)}"
                )
            else:
                for i, entry in enumerate(fields):
                    self._check_entry(
                        f"fields[{i}]",
                        entry,
                        FIELD_KEYS,
                        "field",
                        name_prefix="Field",
                        scalar_keys=frozenset({"location", "type", "units"}),
                    )

        solver = regions.get("solver_settings")
        if solver is not None:
            if not isinstance(solver, dict):
                self.fail(
                    "solver_settings",
                    f"expected an object, got {self._type_name(solver)}",
                )
            else:
                for k, v in solver.items():
                    if not isinstance(v, (str, int, float, bool)):
                        self.fail(
                            f"solver_settings.{k}",
                            f"expected a scalar, got {self._type_name(v)}",
                        )
                    node = f"gph2ccm.Solver.{k}"
                    if isinstance(k, str) and len(node) > MAX_NODE_NAME:
                        self.fail(
                            f"solver_settings.{k}",
                            f"key too long: '{node}' is {len(node)} chars, "
                            f"limit {MAX_NODE_NAME}",
                        )

        mrf = regions.get("mrf")
        if mrf is not None:
            if not isinstance(mrf, list):
                self.fail("mrf", f"expected a list, got {self._type_name(mrf)}")
            else:
                for i, entry in enumerate(mrf):
                    self._check_entry(
                        f"mrf[{i}]",
                        entry,
                        MRF_KEYS,
                        "MRF",
                        name_prefix="MRF",
                        scalar_keys=frozenset(
                            {"region", "type", "axis", "origin", "omega", "units"}
                        ),
                    )

        periodic = regions.get("periodic")
        if periodic is not None:
            if not isinstance(periodic, list):
                self.fail(
                    "periodic", f"expected a list, got {self._type_name(periodic)}"
                )
            else:
                for i, entry in enumerate(periodic):
                    self._check_entry(
                        f"periodic[{i}]",
                        entry,
                        PERIODIC_KEYS,
                        "periodic",
                        name_prefix="Periodic",
                        scalar_keys=frozenset(
                            {"region", "shadow", "type", "axis", "angle", "translation"}
                        ),
                    )


def validate_regions(regions: Any, text: Optional[str] = None) -> list[str]:
    """Return a list of human-readable schema errors (empty == valid)."""
    v = _Validator(text)
    v.validate(regions)
    return v.errors


def assert_valid_regions(regions: Any, text: Optional[str] = None) -> None:
    """Raise :class:`RegionsError` listing every schema error found."""
    errors = validate_regions(regions, text)
    if errors:
        joined = "\n  ".join(errors)
        raise RegionsError(f"invalid regions JSON:\n  {joined}")


def load_regions_checked(path: str | Path) -> dict:
    """``json.load`` + schema validation with line numbers.

    Raises :class:`RegionsError` (not a bare ``ValueError``) so the CLI can
    exit with a clear, actionable message before any conversion work starts.
    """
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"regions JSON not found: {p}")
    text = p.read_text(encoding="utf-8")
    try:
        regions = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RegionsError(
            f"invalid regions JSON ({p}): {exc.msg} at line {exc.lineno} "
            f"column {exc.colno}"
        ) from exc
    assert_valid_regions(regions, text)
    return regions
