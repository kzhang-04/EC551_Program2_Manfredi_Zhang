"""Convert parsed statements into single static assignment (SSA) form.

Every assignment creates a new numbered version of its destination, and every
read refers to the newest version at that point:

    X = A + B        X_1 = A_0 + B_0
    X = X + C        X_2 = X_1 + C_0
    Y = X * A        Y_1 = X_2 * A_0

Version 0 means "the value coming into the program", so any name read before
it is assigned is a primary input. Integer literals pass through unchanged.

By default the outputs are the final version of every assigned variable. An
explicit output list narrows that, which lets later stages reuse registers.

Inputs can also be hardcoded for the demo (``--const C=5``). A hardcoded
input's version 0 is replaced by its value everywhere, so it becomes a
literal and is no longer loaded from the switches.
"""

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

from hls import DATA_WIDTH
from hls.parser import Operand, ParseError, Statement, parse_file


class SSAError(ValueError):
    pass


@dataclass(frozen=True)
class Value:
    """One version of a variable, such as X_2."""

    base: str
    version: int

    @property
    def is_input(self) -> bool:
        return self.version == 0

    def __str__(self) -> str:
        return f"{self.base}_{self.version}"


@dataclass(frozen=True)
class SSAStatement:
    dest: Value
    op: str
    lhs: Value | int
    rhs: Value | int
    line_no: int

    @property
    def sources(self) -> list[Value]:
        """The variable operands, leaving out literals."""
        return [x for x in (self.lhs, self.rhs) if isinstance(x, Value)]

    def __str__(self) -> str:
        return f"{self.dest} = {self.lhs} {self.op} {self.rhs}"


@dataclass(frozen=True)
class SSAProgram:
    statements: list[SSAStatement]
    inputs: list[Value]      # in order of first read
    outputs: list[Value]     # in order of first assignment
    warnings: list[str]
    constants: dict[str, int] = field(default_factory=dict)   # hardcoded inputs


def to_ssa(statements: list[Statement], outputs: list[str] | None = None,
           constants: dict[str, int] | None = None) -> SSAProgram:
    """Rename statements into SSA form.

    ``outputs`` lists the variable names to keep as outputs. None means every
    assigned variable. ``constants`` maps input names to hardcoded values.
    """
    constants = constants or {}
    limit = 2 ** DATA_WIDTH - 1
    for name, value in constants.items():
        if not 0 <= value <= limit:
            raise SSAError(f"--const {name}={value} doesn't fit in {DATA_WIDTH} bits (max {limit})")

    current: dict[str, int] = {}      # newest version of each name
    inputs: list[Value] = []
    hardcoded: set[str] = set()
    ssa: list[SSAStatement] = []

    def read(operand: Operand) -> Value | int:
        if operand.is_const:
            return operand.value
        name = operand.value
        if name not in current and name in constants:
            hardcoded.add(name)
            return constants[name]
        if name not in current:
            current[name] = 0
            inputs.append(Value(name, 0))
        return Value(name, current[name])

    for stmt in statements:
        for operand in (stmt.lhs, stmt.rhs):
            if operand.is_const and operand.value > limit:
                raise SSAError(f"line {stmt.line_no}: literal {operand.value} doesn't fit "
                               f"in {DATA_WIDTH} bits (max {limit})")
        # Operands are renamed before the destination is bumped, so that
        # X = X + 1 reads the old X.
        lhs, rhs = read(stmt.lhs), read(stmt.rhs)
        current[stmt.dest] = current.get(stmt.dest, 0) + 1
        dest = Value(stmt.dest, current[stmt.dest])
        ssa.append(SSAStatement(dest, stmt.op, lhs, rhs, stmt.line_no))

    not_inputs = [name for name in constants if name not in hardcoded]
    if not_inputs:
        names = ", ".join(map(str, inputs)) or "none"
        raise SSAError(
            f"--const {', '.join(not_inputs)}: only inputs can be hardcoded, meaning "
            f"names read before they are assigned (inputs: {names})"
        )

    assigned = list(dict.fromkeys(s.dest.base for s in ssa))
    if outputs is None:
        output_names = assigned
    else:
        unknown = [name for name in outputs if name not in assigned]
        if unknown:
            raise SSAError(
                f"output {', '.join(unknown)} {'is' if len(unknown) == 1 else 'are'} "
                f"never assigned (assigned variables: {', '.join(assigned)})"
            )
        output_names = [name for name in assigned if name in outputs]
    final_outputs = [Value(name, current[name]) for name in output_names]

    return SSAProgram(ssa, inputs, final_outputs, _dead_value_warnings(ssa, final_outputs),
                      dict(constants))


def parse_const_arg(text: str, flag: str = "--const") -> dict[str, int]:
    """Parse text like ``C=5,D=12`` into a dict. ``flag`` names the option
    in error messages."""
    constants: dict[str, int] = {}
    for pair in filter(None, (p.strip() for p in text.split(","))):
        name, eq, value = (s.strip() for s in pair.partition("="))
        if not eq or not name or not value.isdigit():
            raise SSAError(f"{flag} expects NAME=VALUE pairs with whole numbers, got '{pair}'")
        if name in constants:
            raise SSAError(f"{flag} gives {name} more than once")
        constants[name] = int(value)
    return constants


def _dead_value_warnings(ssa: list[SSAStatement], outputs: list[Value]) -> list[str]:
    read = {v for s in ssa for v in s.sources}
    warnings = []
    for stmt in ssa:
        if stmt.dest in read or stmt.dest in outputs:
            continue
        overwrite = next(
            (s for s in ssa if s.dest.base == stmt.dest.base
             and s.dest.version == stmt.dest.version + 1),
            None,
        )
        reason = (f"overwritten on line {overwrite.line_no}" if overwrite
                  else "not an output")
        warnings.append(
            f"line {stmt.line_no}: {stmt.dest} is never read ({reason}); "
            "it is still computed"
        )
    return warnings


def format_ssa(program: SSAProgram, source: str | None = None) -> str:
    lines = []
    if source:
        lines.append(f"# SSA form of {source}")
    lines.append(f"# inputs:  {', '.join(map(str, program.inputs)) or '(none)'}")
    if program.constants:
        consts = ", ".join(f"{name}={value}" for name, value in program.constants.items())
        lines.append(f"# constants: {consts}")
    lines.append(f"# outputs: {', '.join(map(str, program.outputs))}")
    lines += [f"# warning: {w}" for w in program.warnings]
    lines.append("")
    lines += [str(s) for s in program.statements]
    return "\n".join(lines) + "\n"


def export_ssa(program: SSAProgram, path: str | Path, source: str | None = None) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(format_ssa(program, source), encoding="utf-8")
    return path


if __name__ == "__main__":
    ap = argparse.ArgumentParser(prog="python -m hls.ssa",
                                 description="Print or export the SSA form of an equation file.")
    ap.add_argument("equations")
    ap.add_argument("--outputs", help="comma-separated variables to keep as outputs")
    ap.add_argument("--const", metavar="NAME=VALUE,...", help="hardcode inputs, e.g. C=5,D=3")
    ap.add_argument("--export-ssa", nargs="?", const="", metavar="PATH",
                    help="write the SSA to PATH (default generated/<name>/ssa.txt)")
    args = ap.parse_args()

    try:
        outputs = [n.strip() for n in args.outputs.split(",")] if args.outputs else None
        constants = parse_const_arg(args.const) if args.const else None
        program = to_ssa(parse_file(args.equations), outputs, constants)
    except (ParseError, SSAError) as exc:
        sys.exit(str(exc))

    print(format_ssa(program, args.equations), end="")
    if args.export_ssa is not None:
        target = args.export_ssa or Path("generated") / Path(args.equations).stem / "ssa.txt"
        print(f"\nwrote {export_ssa(program, target, args.equations)}")
