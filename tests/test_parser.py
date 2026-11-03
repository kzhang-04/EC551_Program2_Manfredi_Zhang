from pathlib import Path

import pytest

from hls.parser import MAX_EQUATIONS, Operand, ParseError, parse_file, parse_text

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def errors_of(text):
    with pytest.raises(ParseError) as info:
        parse_text(text)
    return info.value.errors


def test_pdf_example():
    stmts = parse_file(EXAMPLES / "pdf_example.txt")
    assert [str(s) for s in stmts] == [
        "X = A + B",
        "Y = X + A",
        "Z = X - B",
        "Q = B * Z",
    ]


def test_fields():
    (stmt,) = parse_text("Out1 = A * 12")
    assert stmt.dest == "Out1"
    assert stmt.op == "*"
    assert stmt.lhs == Operand("A") and not stmt.lhs.is_const
    assert stmt.rhs == Operand(12) and stmt.rhs.is_const
    assert stmt.line_no == 1


@pytest.mark.parametrize("text", ["X=A+B", "  X   =  A  +  B  ", "\tX\t=\tA\t+\tB"])
def test_whitespace_is_optional(text):
    assert str(parse_text(text)[0]) == "X = A + B"


def test_blank_lines_and_comments_are_skipped_but_line_numbers_kept():
    stmts = parse_text("# header\n\nX = A + B  # trailing\n   \nY = X - 1\n")
    assert [s.line_no for s in stmts] == [3, 5]


def test_names_are_case_sensitive():
    (stmt,) = parse_text("x = X + x")
    assert stmt.dest == "x" and stmt.lhs.value == "X" and stmt.rhs.value == "x"


def test_self_assignment_is_allowed():
    assert str(parse_text("A = A + 1")[0]) == "A = A + 1"


def test_two_literals_are_allowed():
    (stmt,) = parse_text("K = 3 * 4")
    assert stmt.lhs.is_const and stmt.rhs.is_const


@pytest.mark.parametrize("char, op", [("−", "-"), ("–", "-"), ("×", "*")])
def test_pasted_lookalike_operators_are_normalized(char, op):
    assert parse_text(f"X = A {char} B")[0].op == op


def test_crlf_and_bom(tmp_path):
    path = tmp_path / "eq.txt"
    path.write_bytes("﻿X = A + B\r\nY = X * 2\r\n".encode("utf-8"))
    assert [str(s) for s in parse_file(path)] == ["X = A + B", "Y = X * 2"]


def test_exactly_max_equations_is_ok():
    text = "\n".join(f"V{i} = A + {i}" for i in range(MAX_EQUATIONS))
    assert len(parse_text(text)) == MAX_EQUATIONS


def test_too_many_equations():
    text = "\n".join(f"V{i} = A + {i}" for i in range(MAX_EQUATIONS + 1))
    (err,) = errors_of(text)
    assert err.line_no == MAX_EQUATIONS + 1
    assert "too many equations" in err.message


@pytest.mark.parametrize("text, message", [
    ("X A + B", "missing '='"),
    ("X = A = B", "more than one '='"),
    ("= A + B", "missing destination"),
    ("X Y = A + B", "exactly one variable name before '='"),
    ("5 = A + B", "destination must be a variable name"),
    ("X = A", "expected an operator"),
    ("X =", "expected an operator"),
    ("X = A B", "expected an operator"),
    ("X = A + B + C", "expected one operator, got 2"),
    ("X = A + -3", "expected one operator, got 2"),
    ("X = A +", "expected exactly 2 operands, got 1"),
    ("X = A B + C", "expected exactly 2 operands, got 3"),
    ("X = + A B", "expected the form"),
    ("X = A / B", "unexpected character '/'"),
    ("X = (A + B)", "unexpected character '('"),
    ("X_1 = A + B", "'_' is not allowed"),
    ("X = 2A + B", "'2A' is not a valid name"),
    ("9Lives = A + B", "'9Lives' is not a valid name"),
])
def test_malformed_lines(text, message):
    (err,) = errors_of(text)
    assert message in err.message
    assert err.line_no == 1


def test_all_errors_are_reported_together():
    errs = errors_of("X = A + B\nY = A /\nZ = X - B\nQ B * Z\n")
    assert [e.line_no for e in errs] == [2, 4]
    text = str(ParseError(errs, "eq.txt"))
    assert text.startswith("eq.txt: 2 errors")
    assert "line 2:" in text and "line 4:" in text


@pytest.mark.parametrize("text", ["", "\n\n", "# only a comment\n"])
def test_empty_input(text):
    (err,) = errors_of(text)
    assert err.message == "no equations found"


def test_missing_file(tmp_path):
    with pytest.raises(ParseError, match="cannot read file"):
        parse_file(tmp_path / "nope.txt")


def test_invalid_example_reports_every_bad_line():
    with pytest.raises(ParseError) as info:
        parse_file(EXAMPLES / "invalid_syntax.txt")
    assert len(info.value.errors) == 4
