from pathlib import Path

import pytest

from hls.parser import parse_file, parse_text
from hls.ssa import SSAError, Value, export_ssa, format_ssa, parse_const_arg, to_ssa

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def ssa_of(text, outputs=None):
    return to_ssa(parse_text(text), outputs)


def lines_of(program):
    return [str(s) for s in program.statements]


def test_pdf_example():
    program = to_ssa(parse_file(EXAMPLES / "pdf_example.txt"))
    assert lines_of(program) == [
        "X_1 = A_0 + B_0",
        "Y_1 = X_1 + A_0",
        "Z_1 = X_1 - B_0",
        "Q_1 = B_0 * Z_1",
    ]
    assert [str(v) for v in program.inputs] == ["A_0", "B_0"]
    assert [str(v) for v in program.outputs] == ["X_1", "Y_1", "Z_1", "Q_1"]
    assert program.warnings == []


def test_reassignment_reads_newest_version():
    program = ssa_of("X = A + B\nX = X + C\nY = X * A")
    assert lines_of(program) == [
        "X_1 = A_0 + B_0",
        "X_2 = X_1 + C_0",
        "Y_1 = X_2 * A_0",
    ]
    assert [str(v) for v in program.outputs] == ["X_2", "Y_1"]


def test_self_assignment_of_an_input():
    program = ssa_of("A = A + 1")
    assert lines_of(program) == ["A_1 = A_0 + 1"]
    assert program.inputs == [Value("A", 0)]
    assert program.outputs == [Value("A", 1)]


def test_input_that_is_assigned_later():
    program = ssa_of("Y = A + B\nA = Y * 2\nZ = A - B")
    assert lines_of(program) == [
        "Y_1 = A_0 + B_0",
        "A_1 = Y_1 * 2",
        "Z_1 = A_1 - B_0",
    ]
    assert [str(v) for v in program.inputs] == ["A_0", "B_0"]


def test_assigned_before_read_is_not_an_input():
    program = ssa_of("X = A + 1\nY = X + X")
    assert program.inputs == [Value("A", 0)]


def test_literals_pass_through():
    (stmt,) = ssa_of("K = 3 * 4").statements
    assert stmt.lhs == 3 and stmt.rhs == 4
    assert stmt.sources == []


def test_sources_skip_literals():
    (stmt,) = ssa_of("Y = X * 2").statements
    assert stmt.sources == [Value("X", 0)]


def test_case_sensitive_names_are_separate_variables():
    program = ssa_of("x = X + 1\nX = x + 1")
    assert lines_of(program) == ["x_1 = X_0 + 1", "X_1 = x_1 + 1"]


def test_outputs_keep_assignment_order():
    program = ssa_of("Q = A + B\nP = A - B\nQ = Q * P")
    assert [str(v) for v in program.outputs] == ["Q_2", "P_1"]


def test_narrowed_outputs():
    program = ssa_of("X = A + B\nY = X + A\nZ = X - B\nQ = B * Z", outputs=["Q", "Y"])
    assert [str(v) for v in program.outputs] == ["Y_1", "Q_1"]


def test_unknown_output_is_an_error():
    with pytest.raises(SSAError, match="output B, W are never assigned"):
        ssa_of("X = A + B", outputs=["X", "B", "W"])


def test_overwritten_value_warns():
    program = ssa_of("X = A + B\nX = C + D")
    (warning,) = program.warnings
    assert warning.startswith("line 1: X_1 is never read (overwritten on line 2)")


def test_unread_non_output_warns():
    program = ssa_of("X = A + B\nY = A - B", outputs=["X"])
    (warning,) = program.warnings
    assert "line 2: Y_1 is never read (not an output)" in warning


def test_format_and_export(tmp_path):
    program = ssa_of("X = A + B\nX = X * 2")
    text = format_ssa(program, "eq.txt")
    assert text.splitlines() == [
        "# SSA form of eq.txt",
        "# inputs:  A_0, B_0",
        "# outputs: X_2",
        "",
        "X_1 = A_0 + B_0",
        "X_2 = X_1 * 2",
    ]
    path = export_ssa(program, tmp_path / "out" / "ssa.txt", "eq.txt")
    assert path.read_text(encoding="utf-8") == text


def test_format_with_no_inputs():
    assert "# inputs:  (none)" in format_ssa(ssa_of("K = 3 * 4"))


def test_reassign_example():
    program = to_ssa(parse_file(EXAMPLES / "reassign.txt"))
    assert lines_of(program) == [
        "X_1 = A_0 + B_0",
        "X_2 = X_1 * 2",
        "A_1 = A_0 + 1",
        "Y_1 = X_2 - A_1",
        "X_3 = Y_1 + X_2",
    ]
    assert [str(v) for v in program.outputs] == ["X_3", "A_1", "Y_1"]


# --- hardcoded inputs (--const) ---------------------------------------------------

def test_const_replaces_input_with_literal():
    program = to_ssa(parse_text("X = A + C\nY = X * C"), constants={"C": 5})
    assert lines_of(program) == ["X_1 = A_0 + 5", "Y_1 = X_1 * 5"]
    assert program.inputs == [Value("A", 0)]
    assert program.constants == {"C": 5}


def test_const_only_replaces_version_zero():
    program = to_ssa(parse_text("A = A + 1\nY = A * 2"), constants={"A": 7})
    assert lines_of(program) == ["A_1 = 7 + 1", "Y_1 = A_1 * 2"]


def test_const_on_a_non_input_is_an_error():
    with pytest.raises(SSAError, match="--const X, W: only inputs can be hardcoded"):
        to_ssa(parse_text("X = A + 1\nY = X + 1"), constants={"X": 1, "W": 2})


def test_const_too_large():
    with pytest.raises(SSAError, match="doesn't fit in 16 bits"):
        to_ssa(parse_text("X = A + C"), constants={"C": 65536})


def test_const_shown_in_export():
    program = to_ssa(parse_text("X = A + C"), constants={"C": 5})
    assert "# constants: C=5" in format_ssa(program)


def test_parse_const_arg():
    assert parse_const_arg("C=5, D = 12,") == {"C": 5, "D": 12}


@pytest.mark.parametrize("text, message", [
    ("C", "NAME=VALUE"),
    ("C=", "NAME=VALUE"),
    ("=5", "NAME=VALUE"),
    ("C=-1", "NAME=VALUE"),
    ("C=1,C=2", "more than once"),
])
def test_parse_const_arg_errors(text, message):
    with pytest.raises(SSAError, match=message):
        parse_const_arg(text)
