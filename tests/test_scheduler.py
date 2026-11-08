import random
from pathlib import Path

import pytest

from hls.dag import KINDS, build_dag
from hls.parser import MAX_EQUATIONS, parse_file, parse_text
from hls.scheduler import (MODES, Schedule, ScheduleError, alap_steps, asap_steps,
                           compare, format_compare, format_schedule, register_count,
                           schedule, validate_schedule)
from hls.ssa import to_ssa

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def dag_of(text, outputs=None):
    return build_dag(to_ssa(parse_text(text), outputs))


def example(name, outputs=None):
    return build_dag(to_ssa(parse_file(EXAMPLES / name), outputs))


def by_dest(dag, sched):
    return {str(n.dest): sched.steps[n.id] for n in dag.nodes}


# --- the worked example from the outline -----------------------------------------

def test_pdf_asap_and_alap():
    dag = example("pdf_example.txt")
    assert by_dest(dag, schedule(dag, "asap")) == {"X_1": 1, "Y_1": 2, "Z_1": 2, "Q_1": 3}
    assert by_dest(dag, schedule(dag, "alap")) == {"X_1": 1, "Y_1": 3, "Z_1": 2, "Q_1": 3}


def test_pdf_hardware_and_registers():
    dag = example("pdf_example.txt")
    sched = schedule(dag, "asap")
    assert sched.fu_counts(dag) == {"add": 1, "sub": 1, "mul": 1}
    assert register_count(dag, sched) == 4          # every value is an output
    narrowed = example("pdf_example.txt", outputs=["Y", "Q"])
    assert register_count(narrowed, schedule(narrowed, "asap")) == 2


def test_pdf_constrained_alap_matches_alap():
    dag = example("pdf_example.txt")
    sched = schedule(dag, "calap", {"add": 1, "sub": 1, "mul": 1})
    assert by_dest(dag, sched) == {"X_1": 1, "Y_1": 3, "Z_1": 2, "Q_1": 3}


# --- unconstrained modes ---------------------------------------------------------

def test_alap_with_longer_bound_leaves_early_steps_idle():
    dag = example("pdf_example.txt")
    sched = schedule(dag, "alap", latency=5)
    assert sched.latency == 5
    assert by_dest(dag, sched) == {"X_1": 3, "Y_1": 5, "Z_1": 4, "Q_1": 5}


def test_alap_bound_below_critical_path():
    dag = example("pdf_example.txt")
    with pytest.raises(ScheduleError, match="shorter than the critical path"):
        schedule(dag, "alap", latency=2)


# --- constrained modes -----------------------------------------------------------

def test_casap_one_adder_stretches_parallel_example():
    dag = example("parallel.txt")
    assert schedule(dag, "asap").latency == 4
    sched = schedule(dag, "casap", {"add": 1})
    assert sched.fu_counts(dag)["add"] == 1
    # four first-level adds take four steps, then mul, add, sub
    assert sched.latency == 7


def test_casap_prefers_the_critical_path():
    dag = dag_of("L = A + B\nM = L + 1\nN = M + 1\nO = C + D")
    sched = schedule(dag, "casap", {"add": 1})
    assert by_dest(dag, sched) == {"L_1": 1, "M_1": 2, "N_1": 3, "O_1": 4}


def test_calap_explicit_bound():
    dag = example("parallel.txt")
    sched = schedule(dag, "calap", {"add": 2, "mul": 1}, latency=8)
    assert sched.latency == 8
    assert sched.fu_counts(dag)["add"] <= 2


def test_calap_bound_too_short():
    dag = example("parallel.txt")
    with pytest.raises(ScheduleError, match="--latency 4 is too short"):
        schedule(dag, "calap", {"add": 1}, latency=4)


def test_zero_limit_for_a_used_kind():
    dag = example("pdf_example.txt")
    with pytest.raises(ScheduleError, match="--mul 0 allows no multipliers"):
        schedule(dag, "casap", {"mul": 0})


def test_zero_limit_for_an_unused_kind_is_fine():
    dag = dag_of("X = A + B")
    assert schedule(dag, "casap", {"mul": 0}).latency == 1


def test_negative_limit():
    with pytest.raises(ScheduleError, match="must not be negative"):
        schedule(dag_of("X = A + B"), "casap", {"add": -1})


# --- custom ----------------------------------------------------------------------

def test_custom_meets_critical_path_with_fewest_units():
    dag = example("pdf_example.txt")
    sched = schedule(dag, "custom")
    assert sched.latency == 3
    assert sched.fu_counts(dag) == {"add": 1, "sub": 1, "mul": 1}


def test_custom_trades_latency_for_units():
    dag = example("parallel.txt")
    fast = schedule(dag, "custom")
    slow = schedule(dag, "custom", latency=7)
    assert fast.latency == 4
    assert slow.latency <= 7
    assert sum(slow.fu_counts(dag).values()) < sum(fast.fu_counts(dag).values())


def test_custom_respects_user_caps():
    dag = example("parallel.txt")
    sched = schedule(dag, "custom", {"add": 1})
    assert sched.fu_counts(dag)["add"] == 1
    assert any("longer than the critical path" in w for w in sched.warnings)


def test_custom_impossible_latency():
    dag = example("parallel.txt")
    with pytest.raises(ScheduleError, match="can't be met"):
        schedule(dag, "custom", {"add": 1}, latency=4)


# --- warnings and errors ---------------------------------------------------------

def test_limits_ignored_warning():
    sched = schedule(example("pdf_example.txt"), "asap", {"add": 1})
    assert "ignored in asap mode" in sched.warnings[0]


def test_unknown_mode():
    with pytest.raises(ScheduleError, match="unknown mode"):
        schedule(dag_of("X = A + B"), "fastest")


def test_validate_catches_dependency_violation():
    dag = example("pdf_example.txt")
    bad = Schedule("asap", {0: 1, 1: 1, 2: 2, 3: 3}, 3, {k: None for k in KINDS})
    with pytest.raises(ScheduleError, match="Y_1 .step 1. reads X_1 .step 1."):
        validate_schedule(dag, bad)


def test_validate_catches_resource_violation():
    dag = dag_of("X = A + B\nY = C + D")
    bad = Schedule("casap", {0: 1, 1: 1}, 1, {"add": 1, "sub": None, "mul": None})
    with pytest.raises(ScheduleError, match="step 1 uses 2 adders, limit is 1"):
        validate_schedule(dag, bad)


def test_validate_catches_missing_and_out_of_range():
    dag = dag_of("X = A + B\nY = X + 1")
    bad = Schedule("asap", {0: 0}, 1, {k: None for k in KINDS})
    with pytest.raises(ScheduleError) as info:
        validate_schedule(dag, bad)
    assert "unscheduled: Y_1" in str(info.value)
    assert "outside 1..1" in str(info.value)


# --- reports ---------------------------------------------------------------------

def test_format_schedule():
    dag = example("pdf_example.txt")
    text = format_schedule(dag, schedule(dag, "alap"))
    assert "mode: alap" in text and "latency: 3 steps" in text
    assert "step 3   Y_1 = X_1 + A_0" in text
    assert "Y_1 = X_1 + A_0  add      2     3         1     3" in text
    assert "hardware: 1 adder, 1 subtractor, 1 multiplier, 4 registers" in text


def test_format_shows_idle_steps():
    dag = dag_of("X = A + B")
    assert "step 1   (idle)" in format_schedule(dag, schedule(dag, "alap", latency=2))


def test_compare_lists_every_mode_and_errors():
    dag = example("parallel.txt")
    rows = compare(dag, {"add": 1}, latency=5)
    assert [mode for mode, _ in rows] == list(MODES)
    text = format_compare(dag, rows)
    assert text.splitlines()[0].split() == ["mode", "latency", "add", "sub", "mul", "FUs", "regs"]
    assert "calap   error:" in text      # 1 adder can't fit in 5 steps
    assert "custom  error:" in text


# --- randomized ------------------------------------------------------------------

def random_equations(rng):
    names = list("ABCDEFGH")
    lines = []
    for _ in range(rng.randint(1, MAX_EQUATIONS)):
        a, b = rng.choice(names), rng.choice(names + ["3"])
        dest = rng.choice(names)
        lines.append(f"{dest} = {a} {rng.choice('+-*')} {b}")
    return "\n".join(lines)


@pytest.mark.parametrize("seed", range(150))
def test_random_programs_schedule_validly(seed):
    rng = random.Random(seed)
    dag = dag_of(random_equations(rng))
    counts = dag.kind_counts()
    limits = {k: rng.randint(1, 3) if counts[k] else None for k in KINDS}
    critical = dag.critical_path()

    early, late = asap_steps(dag), alap_steps(dag, critical)
    assert all(early[i] <= late[i] for i in early)

    for mode in MODES:
        sched = schedule(dag, mode, limits)   # schedule() validates internally
        validate_schedule(dag, sched)
        assert sched.latency >= critical
        assert register_count(dag, sched) >= 1
        if mode in ("casap", "calap", "custom"):
            fus = sched.fu_counts(dag)
            assert all(fus[k] <= limits[k] for k in KINDS if limits[k] is not None)
    assert schedule(dag, "asap").latency == critical
    assert schedule(dag, "custom").latency == critical
