"""Parse an equation file into validated statements.

Each non-blank line holds one equation of the form ``DEST = LHS op RHS``:

* ``op`` is one of ``+``, ``-`` or ``*``.
* ``DEST`` is a variable name.
* ``LHS`` and ``RHS`` are variable names or non-negative integer literals.

Whitespace is optional (``X=A+B`` is fine) and ``#`` starts a comment.
Variable names are case-sensitive, start with a letter, and contain only
letters and digits. Underscores are reserved for SSA version suffixes such as
``X_1``, so the input can't collide with names the tool generates.

All errors in a file are collected and raised together in one ParseError,
so a typo on line 2 doesn't hide another one on line 7.
"""

import re
import sys
from dataclasses import dataclass
from pathlib import Path

MAX_EQUATIONS = 20
OPERATORS = ("+", "-", "*")

# Look-alike characters that appear when equations are pasted from a PDF or
# a word processor.
_NORMALIZE = str.maketrans({"−": "-", "–": "-", "—": "-", "×": "*"})

_TOKEN = re.compile(
    r"\s*(?:(?P<badname>\d+[A-Za-z][A-Za-z0-9]*)|(?P<num>\d+)"
    r"|(?P<name>[A-Za-z][A-Za-z0-9]*)|(?P<sym>[=+\-*]))"
)


@dataclass(frozen=True)
class Operand:
    """A variable reference or an integer literal."""

    value: str | int

    @property
    def is_const(self) -> bool:
        return isinstance(self.value, int)

    def __str__(self) -> str:
        return str(self.value)


@dataclass(frozen=True)
class Statement:
    dest: str
    op: str
    lhs: Operand
    rhs: Operand
    line_no: int

    def __str__(self) -> str:
        return f"{self.dest} = {self.lhs} {self.op} {self.rhs}"


@dataclass(frozen=True)
class LineError:
    line_no: int
    text: str
    message: str

    def __str__(self) -> str:
        return f"line {self.line_no}: {self.message}\n    {self.text}"


class ParseError(Exception):
    def __init__(self, errors: list[LineError], source: str = "<input>"):
        self.errors = errors
        self.source = source
        super().__init__(str(self))

    def __str__(self) -> str:
        count = len(self.errors)
        header = f"{self.source}: {count} error{'s' if count != 1 else ''}"
        return "\n".join([header] + [str(e) for e in self.errors])


def _tokenize(text: str) -> list[tuple[str, str]]:
    """Split a line into (kind, text) tokens, where kind is num, name or sym."""
    tokens, pos = [], 0
    while pos < len(text):
        if text[pos:].strip() == "":
            break
        match = _TOKEN.match(text, pos)
        if not match:
            bad = text[pos:].lstrip()[0]
            if bad == "_":
                raise ValueError(
                    "'_' is not allowed in names (it is reserved for SSA versions)"
                )
            raise ValueError(f"unexpected character '{bad}'")
        kind = match.lastgroup
        if kind == "badname":
            raise ValueError(
                f"'{match.group(kind)}' is not a valid name (names start with a "
                "letter; write '2 * A' for multiplication)"
            )
        tokens.append((kind, match.group(kind)))
        pos = match.end()
    return tokens


def _parse_line(text: str, line_no: int) -> Statement:
    tokens = _tokenize(text)
    kinds = [kind for kind, _ in tokens]

    if "=" not in [tok for _, tok in tokens]:
        raise ValueError("missing '='")
    if [tok for _, tok in tokens].count("=") > 1:
        raise ValueError("more than one '='")
    if tokens[0][1] == "=":
        raise ValueError("missing destination before '='")
    if len(tokens) < 2 or tokens[1][1] != "=":
        raise ValueError("expected exactly one variable name before '='")
    if kinds[0] != "name":
        raise ValueError(f"destination must be a variable name, got '{tokens[0][1]}'")

    rhs = tokens[2:]
    operands = [tok for kind, tok in rhs if kind != "sym"]
    ops = [tok for kind, tok in rhs if kind == "sym"]
    if len(ops) > 1:
        raise ValueError(
            f"expected one operator, got {len(ops)} ({' '.join(ops)}); "
            "split it into separate equations"
        )
    if not ops:
        raise ValueError(
            f"expected an operator ({', '.join(OPERATORS)}) between two operands"
        )
    if len(operands) != 2:
        raise ValueError(f"expected exactly 2 operands, got {len(operands)}")
    if [kind == "sym" for kind, _ in rhs] != [False, True, False]:
        raise ValueError("expected the form DEST = LHS op RHS")

    def operand(kind: str, tok: str) -> Operand:
        return Operand(int(tok) if kind == "num" else tok)

    return Statement(
        dest=tokens[0][1],
        op=rhs[1][1],
        lhs=operand(*rhs[0]),
        rhs=operand(*rhs[2]),
        line_no=line_no,
    )


def parse_text(text: str, source: str = "<input>") -> list[Statement]:
    """Parse equation text, raising ParseError listing every problem found."""
    statements, errors = [], []
    for line_no, raw in enumerate(text.splitlines(), start=1):
        line = raw.split("#", 1)[0].translate(_NORMALIZE).strip()
        if not line:
            continue
        try:
            statement = _parse_line(line, line_no)
        except ValueError as exc:
            errors.append(LineError(line_no, raw.strip(), str(exc)))
            continue
        statements.append(statement)
        if len(statements) == MAX_EQUATIONS + 1:
            errors.append(LineError(
                line_no, raw.strip(),
                f"too many equations (the limit is {MAX_EQUATIONS})",
            ))

    if not statements and not errors:
        errors.append(LineError(0, "", "no equations found"))
    if errors:
        raise ParseError(errors, source)
    return statements


def parse_file(path: str | Path) -> list[Statement]:
    path = Path(path)
    try:
        # utf-8-sig drops the byte-order mark Notepad sometimes adds.
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise ParseError([LineError(0, "", f"cannot read file: {exc.strerror}")], str(path))
    return parse_text(text, str(path))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python -m hls.parser <equations.txt>")
    try:
        for stmt in parse_file(sys.argv[1]):
            print(f"line {stmt.line_no:>2}: {stmt}")
    except ParseError as exc:
        sys.exit(str(exc))
