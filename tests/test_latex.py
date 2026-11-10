"""``export --format latex``: numbers for a paper that can only come from verified outputs."""

from __future__ import annotations

import json
import sys

import pytest

from sciproof import cli
from sciproof.errors import SciProofError, UsageError
from sciproof.latex import MACRO_NAME, _auto_macro_name, format_value, to_latex
from sciproof.numeric import NumericFormatError

RESULT = "out/result.json"


def _values(*triples):
    return [tuple(t) for t in triples]


def test_values_become_macros_after_a_passing_verification(project):
    project.capture()
    text = to_latex(
        project.manifest,
        _values(("answer", RESULT, "$.value"), ("tag", RESULT, "$.label")),
    )
    lines = text.splitlines()
    assert lines[0].startswith("% SciProof ") and "integrity verification: PASS" in lines[0]
    assert "\\newcommand{\\answer}{42}" in lines
    assert "\\newcommand{\\tag}{ok}" in lines


def test_nothing_is_written_when_the_output_was_modified(project):
    project.capture()
    (project.root / RESULT).write_text(json.dumps({"value": 99.0, "label": "ok"}))
    with pytest.raises(SciProofError, match="not exporting values.*FAIL"):
        to_latex(project.manifest, _values(("answer", RESULT, "$.value")))


def test_csv_cells_are_read_by_row_and_column(project):
    script = "import pathlib; pathlib.Path('t.csv').write_text('name,acc\\nrun1,0.91\\n')"
    project.capture(
        argv=[sys.executable, "-c", script],
        inputs=[],
        outputs=[],
        numeric_outputs=["t.csv"],
    )
    text = to_latex(project.manifest, _values(("acc", "t.csv", "r1c1"), ("who", "t.csv", "r1c0")))
    assert "\\newcommand{\\acc}{0.91}" in text and "\\newcommand{\\who}{run1}" in text


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (42, "42"),
        (0.93, "0.93"),
        (1.5e-5, "$1.5\\times 10^{-5}$"),
        (2.5e8, "$2.5\\times 10^{8}$"),
        (0.0, "0"),
        (float("nan"), "NaN"),
        (float("inf"), "$\\infty$"),
        (True, "true"),
        (None, "null"),
        ("a_b & 50% #1 $x$ {y}", "a\\_b \\& 50\\% \\#1 \\$x\\$ \\{y\\}"),
        ("two\nlines", "two lines"),
        (10**400, "1" + "0" * 400),
    ],
)
def test_values_are_written_as_safe_latex(value, expected):
    assert format_value(value) == expected


@pytest.mark.parametrize("name", ["", "a1", "my_value", "x-y", "a" * 41, "\\def", "über"])
def test_a_macro_name_is_letters_only(project, name):
    project.capture()
    with pytest.raises(UsageError, match="macro name"):
        to_latex(project.manifest, _values((name, RESULT, "$.value")))


def test_names_must_be_unique_and_values_given(project):
    project.capture()
    with pytest.raises(UsageError, match="more than once"):
        to_latex(project.manifest, _values(("a", RESULT, "$.value"), ("a", RESULT, "$.label")))
    with pytest.raises(UsageError, match="at least one"):
        to_latex(project.manifest, [])


@pytest.mark.parametrize(
    ("output", "location", "message"),
    [
        ("data.json", "$.x", "not a declared output"),  # an input
        ("nope.json", "$.x", "not a declared output"),
        ("out/exact.txt", "$.x", "not a location"),  # hashed exactly, not readable as values
        (RESULT, "$..value", "not a location"),
        (RESULT, "r1c1", "not a location"),
        (RESULT, "$.missing", "not present"),
        ("../x.json", "$.x", "outside|'\\.\\.'"),
    ],
)
def test_only_declared_readable_locations_can_be_exported(project, output, location, message):
    project.capture()
    with pytest.raises(SciProofError, match=message):
        to_latex(project.manifest, _values(("v", output, location)))


def test_cli_writes_a_file_and_refuses_to_mix_formats(project, capsys):
    project.capture()
    code = cli.main(
        ["export", project.manifest.name, "--format", "latex", "--value", "answer", RESULT]
        + ["$.value", "-o", "numbers.tex"]
    )
    assert code == cli.EXIT_OK, capsys.readouterr().err
    assert "\\newcommand{\\answer}{42}" in (project.root / "numbers.tex").read_text()
    assert cli.main(["export", project.manifest.name, "--value", "a", RESULT, "$.value"]) == (
        cli.EXIT_ERROR
    )
    assert "only used with --format latex" in capsys.readouterr().err
    assert cli.main(["export", project.manifest.name, "--format", "latex"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert r"\newcommand{\Value}{42}" in out


def test_auto_export_with_no_numeric_outputs_fails(project, capsys):
    project.capture(name="no_num", inputs=[], outputs=["out/exact.txt"], numeric_outputs=[])
    code = cli.main(["export", "no_num.sciproof.json", "--format", "latex"])
    assert code == cli.EXIT_ERROR
    assert "no numeric outputs found in manifest" in capsys.readouterr().err


def test_empty_values_list_raises_usage_error(project):
    project.capture()
    with pytest.raises(UsageError, match="give at least one --value"):
        to_latex(project.manifest, [])


def test_more_than_the_maximum_number_of_values_is_refused(project):
    project.capture()
    values = [
        ("v" + "".join(chr(97 + int(d)) for d in str(i)), RESULT, "$.value") for i in range(201)
    ]
    with pytest.raises(UsageError, match="at most 200"):
        to_latex(project.manifest, values)


def test_an_unreadable_numeric_output_is_reported_with_its_name(project, monkeypatch):
    project.capture()

    def unreadable(*args, **kwargs):
        raise NumericFormatError("not valid JSON")

    monkeypatch.setattr("sciproof.latex.load_numeric", unreadable)
    with pytest.raises(SciProofError, match="result.json"):
        to_latex(project.manifest, _values(("answer", RESULT, "$.value")))


def test_auto_macro_name_resolves_collisions_with_stem_and_letters():
    existing = set()
    name1 = _auto_macro_name("$.loss", "eval.json", existing)
    assert name1 == "Loss"
    assert "Loss" in existing

    # Second collision tries output stem
    name2 = _auto_macro_name("$.loss", "eval.json", existing)
    assert name2 == "EvalLoss"
    assert "EvalLoss" in existing

    # Third collision disambiguates with letter suffix
    name3 = _auto_macro_name("$.loss", "eval.json", existing)
    assert name3 == "EvalLossB"
    assert "EvalLossB" in existing

    name4 = _auto_macro_name("$.loss", "eval.json", existing)
    assert name4 == "EvalLossC"
    assert "EvalLossC" in existing


def test_auto_macro_name_bounded_and_always_valid_latex():
    existing = {f"Loss{chr(65 + i)}" for i in range(26)}
    existing.add("Loss")
    existing.add("EvalLoss")
    original_existing = set(existing)
    name = _auto_macro_name("$.loss", "eval.json", existing)
    assert MACRO_NAME.fullmatch(name)
    assert name not in original_existing
    assert name in existing


def test_auto_macro_name_edge_cases():
    name = _auto_macro_name("$.0_accuracy", "eval.json", set())
    assert name.startswith("Eval") and "Accuracy" in name
    assert MACRO_NAME.fullmatch(name)

    assert _auto_macro_name("$.__", "123.json", set()) == "Metric"


def test_auto_export_nested_json_and_csv_succeeds(project):
    script = (
        "import json, pathlib\n"
        "pathlib.Path('out').mkdir(exist_ok=True)\n"
        "with open('out/nested.json', 'w') as f:\n"
        "    json.dump({'items': [1, 2], 'metrics': {'loss': 0.1}}, f)\n"
        "pathlib.Path('out/table.csv').write_text('a,b\\n1,2\\n3,4\\n')\n"
    )
    project.capture(
        argv=[sys.executable, "-c", script],
        inputs=[],
        outputs=[],
        numeric_outputs=["out/nested.json", "out/table.csv"],
    )
    text = to_latex(project.manifest)
    assert "\\newcommand{\\" in text
    assert "integrity verification: PASS" in text
    assert "#len" not in text
    assert "#keys" not in text
    assert "#cols" not in text
