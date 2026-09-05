# ruff: noqa
"""F5 -- property-based (hypothesis) invariants over the regions JSON schema
and the macro generator.

Optional dependency: the whole module is skipped when ``hypothesis`` is not
installed, so plain-CI environments keep passing.  Run with pytest for the
rich report or via ``tests/test_writer.py``'s main() (it imports these tests
dynamically when available).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

hypothesis = pytest.importorskip("hypothesis")

from hypothesis import given, settings, strategies as st  # noqa: E402

from gph2ccm.macro import (  # noqa: E402
    BC_PARAM_TO_PROFILE,
    _norm_param_key,
    generate_macro,
    sanitize_class_name,
)
from gph2ccm.regions_schema import (  # noqa: E402
    RegionsError,
    assert_valid_regions,
    validate_regions,
)


# -- strategies ---------------------------------------------------------------

_region_names = st.from_regex(r"[A-Za-z][A-Za-z0-9_]{0,15}", fullmatch=True)

_bctypes = st.sampled_from(
    ["wall", "inlet", "outlet", "symmetry", "velocity-inlet",
     "pressure-outlet", "periodic", "slide", ""]
)

_params = st.dictionaries(
    st.from_regex(r"[A-Za-z][A-Za-z0-9_]{0,11}", fullmatch=True),
    st.one_of(st.floats(allow_nan=False, allow_infinity=False,
                        min_value=-1e6, max_value=1e6),
              st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789 .",
                      min_size=1, max_size=12)),
    max_size=4,
)

_regions_payload = st.fixed_dictionaries(
    {},
    optional={
        "fluid_regions": st.lists(_region_names, max_size=3, unique=True),
        "solid_regions": st.lists(_region_names, max_size=3, unique=True),
        "boundary_conditions": st.dictionaries(
            _region_names,
            st.fixed_dictionaries(
                {},
                optional={"type": _bctypes, "params": _params},
            ),
            max_size=4,
        ),
        "solver_settings": st.dictionaries(
            st.from_regex(r"[A-Za-z][A-Za-z0-9_]{0,11}", fullmatch=True),
            st.one_of(st.booleans(), st.floats(allow_nan=False)),
            max_size=4,
        ),
    },
)


# -- invariants ---------------------------------------------------------------

@given(payload=_regions_payload)
@settings(max_examples=60, deadline=None)
def test_valid_regions_never_raise(payload):
    """Any dict built from the schema's own key/value shapes must validate."""
    validate_regions(payload)


@given(payload=st.dictionaries(
    st.text(min_size=1, max_size=8), st.integers(), max_size=3))
@settings(max_examples=40, deadline=None)
def test_garbage_regions_raise_regionserror(payload):
    """Non-schema keys must be rejected with RegionsError (never TypeError)."""
    if set(payload) <= {"fluid_regions", "solid_regions",
                        "boundary_conditions", "fields", "solver_settings",
                        "mrf", "periodic"}:
        return
    with pytest.raises(RegionsError):
        assert_valid_regions(payload)


@given(name=st.text(min_size=0, max_size=40))
@settings(max_examples=100, deadline=None)
def test_class_name_is_always_valid_java(name):
    """sanitize_class_name output must be a usable Java identifier."""
    out = sanitize_class_name(name)
    assert out
    assert out[0].isdigit() is False
    assert all(c.isalnum() or c in "_$" for c in out)


@given(
    key=st.from_regex(r"[A-Za-z][A-Za-z0-9_\- ]{0,15}", fullmatch=True),
    value=st.floats(allow_nan=False, allow_infinity=False,
                    min_value=-1e6, max_value=1e6),
)
@settings(max_examples=60, deadline=None)
def test_known_numeric_params_become_setvalue(key, value):
    """A param whose normalised key is in the profile map and whose value is
    a plain number must produce exactly one setValue call, never a TODO."""
    meta = {
        "file": "h", "fields": [], "solver_settings": {}, "mrf": [],
        "periodic": [], "notes": {}, "quality": {},
        "boundary_conditions": [
            {"label": "b", "type": "inlet", "params": {key: value}},
        ],
    }
    src = generate_macro(meta, ccm_path=None)
    norm = _norm_param_key(key)
    if norm in BC_PARAM_TO_PROFILE:
        assert ".setValue(" in src
        assert "gph2ccm TODO" not in src
    else:
        assert ".setValue(" not in src
        assert "gph2ccm TODO" in src


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
