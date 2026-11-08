import random
from pathlib import Path

import pytest

from hls.binding import bind
from hls.control import build_control, control_signals, sel_width
from hls.dag import KINDS, build_dag
from hls.golden import GoldenError, evaluate, random_vectors
from hls.parser import parse_file, parse_text
from hls.scheduler import MODES, register_count, schedule
from hls.ssa import Value, to_ssa
from tests.test_scheduler import random_equations

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def pdf_binding(outputs=None, mode="asap"):
    dag = build_dag(to_ssa(parse_file(EXAMPLES / "pdf_example.txt"), outputs))
    sched = schedule(dag, mode)
    return dag, sched, bind(dag, sched)


def test_worked_example_registers_share():
    dag, _, b = pdf_binding(outputs=["Y", "Q"])
    held = [[str(dag.nodes[v].dest) for v in r.values] for r in b.regs]
    assert held == [["X_1", "Y_1"], ["Z_1", "Q_1"]]


def test_worked_example_muxes():
    _, _, b = pdf_binding(outputs=["Y", "Q"])
    add0 = b.fus[0]
    # Y = X + A is swapped to A + X, so port a always reads A.
    assert [s.label for s in add0.a_sources] == ["A"]
    assert [s.label for s in add0.b_sources] == ["B", "r0"]
    assert [b.fus[i].name for i in b.regs[1].writers] == ["sub0", "mul0"]
    assert b.mux_inputs() == 4


def test_subtraction_is_never_swapped():
    dag = build_dag(to_ssa(parse_text("P = A - B\nQ = B - A")))
    sched = schedule(dag, "casap", {"sub": 1})
    b = bind(dag, sched)
    assert [(a.label, bb.label) for a, bb in (b.op_ports[0], b.op_ports[1])] == [("A", "B"), ("B", "A")]


def test_every_value_is_an_output_means_no_sharing():
    _, _, b = pdf_binding()
    assert len(b.regs) == 4


def test_constants_become_sources():
    dag = build_dag(to_ssa(parse_text("Y = A * 3")))
    b = bind(dag, schedule(dag, "asap"))
    assert b.fus[0].b_sources[0].name == "16'd3"


def test_control_table():
    dag, sched, b = pdf_binding(outputs=["Y", "Q"])
    signals = control_signals(b)
    assert [s.name for s in signals] == ["add0_b_sel", "r0_we", "r1_we", "r1_sel"]
    states = build_control(dag, sched, b)
    assert [st.values for st in states] == [
        {"add0_b_sel": 0, "r0_we": 1},
        {"add0_b_sel": 1, "r0_we": 1, "r1_we": 1, "r1_sel": 0},
        {"r1_we": 1, "r1_sel": 1},
    ]
    assert [st.active for st in states] == [[0], [0, 1], [2]]


@pytest.mark.parametrize("n, width", [(2, 1), (3, 2), (4, 2), (5, 3), (8, 3), (9, 4)])
def test_sel_width(n, width):
    assert sel_width(n) == width


@pytest.mark.parametrize("seed", range(100))
def test_random_bindings(seed):
    rng = random.Random(seed)
    dag = build_dag(to_ssa(parse_text(random_equations(rng))))
    counts = dag.kind_counts()
    limits = {k: rng.randint(1, 3) if counts[k] else None for k in KINDS}
    for mode in MODES:
        sched = schedule(dag, mode, limits)
        b = bind(dag, sched)                 # bind() validates itself
        assert len(b.regs) == register_count(dag, sched)
        assert all(fu.a_sources and fu.b_sources for fu in b.fus)


# --- golden model ----------------------------------------------------------------

def test_evaluate_wraps_and_warns():
    program = to_ssa(parse_text("D = A - B\nP = A * B"))
    values, warnings = evaluate(program, {"A": 1, "B": 2})
    assert values[Value("D", 1)] == 0xFFFF
    assert "negative" in warnings[0]
    values, warnings = evaluate(program, {"A": 255, "B": 255})
    assert values[Value("P", 1)] == 65025 and warnings == []


def test_evaluate_overflow_warning():
    program = to_ssa(parse_text("P = A * B\nQ = P * P"))
    values, warnings = evaluate(program, {"A": 255, "B": 255})
    assert values[Value("Q", 1)] == (65025 * 65025) & 0xFFFF
    assert "overflows" in warnings[0]


@pytest.mark.parametrize("vector, message", [
    ({"A": 1}, "missing B"),
    ({"A": 1, "B": 2, "C": 3}, "C is not"),
    ({"A": 256, "B": 0}, "won't fit"),
])
def test_bad_vectors(vector, message):
    program = to_ssa(parse_text("X = A + B"))
    with pytest.raises(GoldenError, match=message):
        evaluate(program, vector)


def test_random_vectors_are_repeatable_and_in_range():
    program = to_ssa(parse_text("X = A + B"))
    first, second = random_vectors(program, 5), random_vectors(program, 5)
    assert first == second
    assert all(0 <= v <= 255 for vec in first for v in vec.values())
