"""Reference model: evaluate an SSA program in Python.

The generated testbench and trace compare the hardware against these
values. All arithmetic wraps modulo 2**DATA_WIDTH, like the Verilog units.
"""

import random

from hls import DATA_WIDTH, INPUT_WIDTH
from hls.ssa import SSAProgram, Value

MASK = 2 ** DATA_WIDTH - 1


class GoldenError(ValueError):
    pass


def evaluate(program: SSAProgram, inputs: dict[str, int]) -> tuple[dict[Value, int], list[str]]:
    """Return every value in the program, plus warnings for any result that
    went negative or overflowed and had to wrap."""
    check_vector(program, inputs)
    values = {v: inputs[v.base] for v in program.inputs}
    warnings = []

    def get(x: Value | int) -> int:
        return x if isinstance(x, int) else values[x]

    for s in program.statements:
        a, b = get(s.lhs), get(s.rhs)
        raw = a + b if s.op == "+" else a - b if s.op == "-" else a * b
        if raw < 0:
            warnings.append(f"{s.dest} = {a} {s.op} {b} is negative and wraps to {raw & MASK}")
        elif raw > MASK:
            warnings.append(f"{s.dest} = {a} {s.op} {b} = {raw} overflows "
                            f"{DATA_WIDTH} bits and wraps to {raw & MASK}")
        values[s.dest] = raw & MASK
    return values, warnings


def check_vector(program: SSAProgram, vector: dict[str, int]) -> None:
    names = [v.base for v in program.inputs]
    missing = [n for n in names if n not in vector]
    extra = [n for n in vector if n not in names]
    limit = 2 ** INPUT_WIDTH - 1
    big = [f"{n}={v}" for n, v in vector.items() if not 0 <= v <= limit]
    problems = []
    if missing:
        problems.append(f"missing {', '.join(missing)}")
    if extra:
        problems.append(f"{', '.join(extra)} {'is' if len(extra) == 1 else 'are'} not "
                        f"switch inputs (inputs: {', '.join(names) or 'none'})")
    if big:
        problems.append(f"{', '.join(big)} won't fit on SW[{INPUT_WIDTH - 1}:0] (max {limit})")
    if problems:
        raise GoldenError("test values: " + "; ".join(problems))


def random_vectors(program: SSAProgram, count: int, seed: int = 551) -> list[dict[str, int]]:
    rng = random.Random(seed)
    limit = 2 ** INPUT_WIDTH - 1
    return [{v.base: rng.randint(0, limit) for v in program.inputs} for _ in range(count)]
