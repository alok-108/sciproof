"""Bounded generated inputs for the public file parsers and canonical writer."""

import copy
import json
import tempfile
from contextlib import suppress
from pathlib import Path, PurePosixPath, PureWindowsPath

from hypothesis import example, given, settings
from hypothesis import strategies as st

from sciproof.errors import SciProofError, UnsafePathError
from sciproof.manifest import load_manifest, write_manifest
from sciproof.paths import validate_relative_path
from sciproof.policy import PolicyError, load_policy, parse_policy

BOUNDED = settings(max_examples=50, deadline=None, derandomize=True)
JSON_VALUES = st.recursive(
    st.none()
    | st.booleans()
    | st.integers()
    | st.floats(allow_nan=False, allow_infinity=False)
    | st.text(max_size=40),
    lambda children: (
        st.lists(children, max_size=5) | st.dictionaries(st.text(max_size=20), children, max_size=5)
    ),
    max_leaves=20,
)


def _load_or_refuse(content):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "input.json"
        path.write_bytes(content)
        try:
            loaded = load_manifest(path)
        except SciProofError:
            return
        # Every accepted file must also survive the public validating writer.
        target = Path(directory) / "canonical.json"
        write_manifest(target, loaded)
        assert load_manifest(target) == loaded


@BOUNDED
@given(st.binary(max_size=2048))
@example(b"{" + b'"x":[' * 1500 + b"0" + b"]}" * 1500)
@example(b'{"schema": 1, "schema": 2}')
@example(b'{"x": NaN, "y": Infinity}')
@example(b'{"x": "\\ud800"}')
@example(b'{"x": ' + b"9" * 5000 + b"}")
def test_manifest_bytes_are_loaded_or_cleanly_refused(content):
    _load_or_refuse(content)


@BOUNDED
@given(JSON_VALUES)
def test_structured_json_is_loaded_or_cleanly_refused(value):
    _load_or_refuse(json.dumps(value).encode("utf-8"))


def test_captured_manifest_has_a_canonical_round_trip(project):
    baseline = load_manifest(project.capture().manifest_path)

    @BOUNDED
    @given(
        note=st.text(alphabet="abc XYZ012éλ", min_size=1, max_size=60),
        arguments=st.lists(st.text(alphabet="abc XYZ012éλ", min_size=1, max_size=30), max_size=8),
        packages=st.dictionaries(
            st.text(alphabet="abcdef", min_size=1, max_size=10),
            st.integers(min_value=0, max_value=1000),
            max_size=5,
        ),
    )
    def round_trip(note, arguments, packages):
        data = copy.deepcopy(baseline)
        data["schema_version"] = "1.1"
        data["note"] = "note: " + note
        data["command"]["argv"] = ["python", *arguments]
        data["dependencies"] = [
            {"name": name, "version": str(version)} for name, version in packages.items()
        ]
        with tempfile.TemporaryDirectory() as directory:
            first, second = (Path(directory) / name for name in ("first.json", "second.json"))
            write_manifest(first, data)
            loaded = load_manifest(first)
            assert loaded == data
            write_manifest(second, loaded)
            assert first.read_bytes() == second.read_bytes()

    round_trip()


def test_duplicate_keys_in_otherwise_valid_manifest_are_refused(project):
    data = load_manifest(project.capture().manifest_path)

    @BOUNDED
    @given(st.text(max_size=40))
    def duplicate(value):
        raw = json.dumps(data)
        # A valid base makes acceptance of the repeated key observable.
        raw = raw[:-1] + ', "name": ' + json.dumps(value) + "}"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "duplicate.json"
            path.write_text(raw, encoding="utf-8")
            try:
                load_manifest(path)
            except SciProofError as exc:
                assert "duplicate object key" in str(exc)
            else:
                raise AssertionError("a duplicated manifest name was accepted")

    duplicate()


@BOUNDED
@given(st.binary(max_size=2048))
@example(b"version = 1\n[manifest]\nmax_age_days = nan\n")
@example(b"version = 1\n[manifest]\nmax_age_days = " + b"1" + b"0" * 400 + b"\n")
@example(b"version = 1\n[outputs]\nmax_rtol = " + b"1" + b"0" * 400 + b"\n")
@example(b"version = 1\n[outputs]\nmax_atol = inf\n")
@example(b"version = 1\n[outputs]\nmax_atol = " + b"1" + b"0" * 400 + b"\n")
@example(b"version = 1\nversion = 1\n")
def test_policy_bytes_are_loaded_or_cleanly_refused(content):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "policy.toml"
        path.write_bytes(content)
        with suppress(PolicyError):
            load_policy(path)


@BOUNDED
@given(
    age=st.integers(min_value=1, max_value=10**6),
    exit_code=st.integers(min_value=-(2**31), max_value=2**31 - 1),
    required=st.booleans(),
    tolerance=st.floats(min_value=0, max_value=1, allow_nan=False),
    outputs=st.lists(st.integers(min_value=0, max_value=100), unique=True, max_size=5),
)
def test_valid_policy_rules_survive_reserialisation(age, exit_code, required, tolerance, outputs):
    rules = {
        "manifest": {"max_age_days": age, "require_exit_code": exit_code},
        "outputs": {"max_atol": tolerance, "require_numeric": [f"out/{n}.json" for n in outputs]},
        "rerun": {"require": required},
    }

    def serialise(sections):
        lines = ["version = 1"]
        for section, values in sections.items():
            lines.append(f"[{section}]")
            lines.extend(f"{key} = {json.dumps(value)}" for key, value in values.items())
        return "\n".join(lines) + "\n"

    parsed = parse_policy(serialise(rules))
    assert parsed.rules == rules
    assert parse_policy(serialise(parsed.rules)).rules == rules


@BOUNDED
@given(st.text(max_size=200))
@example("../outside")
@example("C:/outside")
@example("a/../../outside")
@example("a\\..\\outside")
def test_accepted_relative_path_is_lexically_contained(value):
    try:
        accepted = validate_relative_path(value)
    except UnsafePathError:
        return
    assert accepted == value
    assert not PurePosixPath(accepted).is_absolute()
    assert not PureWindowsPath(accepted).drive
    assert all(part not in ("", ".", "..") for part in accepted.split("/"))
    # This is lexical containment; resolving existing symlinks is resolve_inside's job.
    base = Path(tempfile.gettempdir()) / "sciproof-property-root"
    assert (base / accepted).is_relative_to(base)
