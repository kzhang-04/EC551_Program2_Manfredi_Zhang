"""Assign every DAG node to a control step (one clock cycle).

Every operation takes exactly one step, and a node runs in a later step than
every node it reads. Five modes are available:

* ``asap``:   each op as early as its inputs allow. Unlimited units.
* ``alap``:   each op as late as possible within a latency bound. Unlimited units.
* ``casap``:  forward list scheduling with at most N units of each kind.
* ``calap``:  the same list scheduler run backward from the latency bound.
* ``custom``: finds the fewest units that still meet a latency target. It
  schedules least-slack ops first and breaks ties by register pressure.

Resource limits are per kind (add, sub, mul). None means unlimited.
"""

import argparse
import math
import sys
from collections import Counter
from dataclasses import dataclass, field

from hls.dag import DAG, DAGError, KINDS, UNIT_NAME, Node, build_dag
from hls.parser import ParseError, parse_file
from hls.ssa import SSAError, parse_const_arg, to_ssa

MODES = ("asap", "casap", "alap", "calap", "custom")
CONSTRAINED = ("casap", "calap", "custom")

Limits = dict[str, int | None]


class ScheduleError(ValueError):
    pass


@dataclass
class Schedule:
    mode: str
    steps: dict[int, int]           # node id -> step, starting at 1
    latency: int
    limits: Limits                  # what the schedule was held to
    warnings: list[str] = field(default_factory=list)

    def nodes_in_step(self, dag: DAG, step: int) -> list[Node]:
        return [n for n in dag.nodes if self.steps[n.id] == step]

    def fu_counts(self, dag: DAG) -> dict[str, int]:
        """Units needed per kind: the most ops of that kind in any one step."""
        per_step = Counter((self.steps[n.id], n.kind) for n in dag.nodes)
        return {k: max((c for (_, kind), c in per_step.items() if kind == k), default=0)
                for k in KINDS}


# --- unconstrained -------------------------------------------------------------

def asap_steps(dag: DAG) -> dict[int, int]:
    steps: dict[int, int] = {}
    for n in dag.topo_order():
        steps[n.id] = 1 + max((steps[p] for p in n.preds), default=0)
    return steps


def alap_steps(dag: DAG, latency: int) -> dict[int, int]:
    critical = dag.critical_path()
    if latency < critical:
        raise ScheduleError(
            f"latency {latency} is shorter than the critical path ({critical} steps)"
        )
    steps: dict[int, int] = {}
    for n in reversed(dag.topo_order()):
        steps[n.id] = min((steps[s] for s in n.succs), default=latency + 1) - 1
    return steps


def heights(dag: DAG) -> dict[int, int]:
    """Length of the longest chain from each node down to a sink, counting itself."""
    h: dict[int, int] = {}
    for n in reversed(dag.topo_order()):
        h[n.id] = 1 + max((h[s] for s in n.succs), default=0)
    return h


# --- list scheduling -----------------------------------------------------------

def _list_schedule(dag: DAG, limits: Limits, key, reverse: bool = False) -> dict[int, int]:
    """Fill steps one at a time from the ready nodes, in ``key`` order.

    A node is ready once everything it depends on sits in an earlier step.
    With ``reverse`` the graph is walked from the sinks up, and the returned
    steps count backward from the end.
    """
    deps = (lambda n: n.succs) if reverse else (lambda n: n.preds)
    steps: dict[int, int] = {}
    step = 0
    while len(steps) < len(dag.nodes):
        step += 1
        ready = [n for n in dag.nodes if n.id not in steps
                 and all(d in steps for d in deps(n))]
        ready.sort(key=lambda n: key(n, steps))
        used = Counter()
        for n in ready:
            limit = limits.get(n.kind)
            if limit is None or used[n.kind] < limit:
                steps[n.id] = step
                used[n.kind] += 1
    return steps


def _check_limits(dag: DAG, limits: Limits) -> None:
    counts = dag.kind_counts()
    for kind, limit in limits.items():
        if limit is None:
            continue
        if limit < 0:
            raise ScheduleError(f"--{kind} must not be negative")
        if limit == 0 and counts[kind]:
            raise ScheduleError(
                f"--{kind} 0 allows no {UNIT_NAME[kind]}s, but the equations "
                f"use {counts[kind]} {kind} operation{'s' if counts[kind] != 1 else ''}"
            )


def casap_steps(dag: DAG, limits: Limits) -> dict[int, int]:
    h = heights(dag)
    # Longest remaining chain first, so the critical path isn't delayed.
    return _list_schedule(dag, limits, key=lambda n, _: (-h[n.id], n.id))


def calap_steps(dag: DAG, limits: Limits, latency: int | None) -> tuple[dict[int, int], int]:
    depth = asap_steps(dag)
    backward = _list_schedule(dag, limits, key=lambda n, _: (-depth[n.id], n.id), reverse=True)
    needed = max(backward.values())
    if latency is None:
        # Default to the constrained ASAP latency, as plain ALAP defaults to
        # the plain ASAP latency.
        latency = max(needed, max(casap_steps(dag, limits).values()))
    elif needed > latency:
        raise ScheduleError(
            f"constrained ALAP needed {needed} steps with these resource limits, "
            f"so --latency {latency} is too short"
        )
    return {i: latency + 1 - s for i, s in backward.items()}, latency


def custom_steps(dag: DAG, limits: Limits, latency: int | None) -> tuple[dict[int, int], Limits, list[str]]:
    """Fewest units that meet the latency target.

    The target is ``latency``, or the critical path if none is given. Each kind
    starts at its lower bound (ceil(ops / target)), with user limits as caps.
    The loop runs the list scheduler and, while the target is missed, adds one
    unit of the kind of the earliest op that ran past its ALAP deadline.

    Ready ops are ordered by least slack, meaning earliest ALAP deadline.
    Ties go to the op that frees the most registers, i.e. the last reader of
    the most values that aren't outputs.
    """
    critical = dag.critical_path()
    target = latency if latency is not None else critical
    deadline = alap_steps(dag, target)

    def frees(n: Node, steps: dict[int, int]) -> int:
        return sum(
            1 for p in n.preds
            if not dag.nodes[p].is_output
            and all(r in steps for r in dag.nodes[p].succs if r != n.id)
        )

    def key(n, steps):
        return (deadline[n.id], -frees(n, steps), n.id)

    counts = dag.kind_counts()
    caps = {k: min(limits.get(k) or counts[k], counts[k]) for k in KINDS}
    current = {k: min(caps[k], math.ceil(counts[k] / target)) for k in KINDS}

    while True:
        steps = _list_schedule(dag, current, key)
        late = sorted((n for n in dag.nodes if steps[n.id] > deadline[n.id]),
                      key=lambda n: (steps[n.id], n.id))
        if not late:
            return steps, current, []
        grow = next((n.kind for n in late if current[n.kind] < caps[n.kind]), None)
        if grow is None:
            achieved = max(steps.values())
            if latency is not None:
                raise ScheduleError(
                    f"custom: --latency {latency} can't be met with these resource "
                    f"limits (best found: {achieved} steps)"
                )
            return steps, current, [
                f"latency {achieved} is longer than the critical path ({critical}) "
                "because of the resource limits"
            ]
        current[grow] += 1


# --- entry point and checks ----------------------------------------------------

def schedule(dag: DAG, mode: str, limits: Limits | None = None,
             latency: int | None = None) -> Schedule:
    if mode not in MODES:
        raise ScheduleError(f"unknown mode '{mode}' (choose from {', '.join(MODES)})")
    limits = {k: (limits or {}).get(k) for k in KINDS}
    unlimited = {k: None for k in KINDS}
    has_limits = any(v is not None for v in limits.values())
    warnings = []
    if latency is not None and latency < 1:
        raise ScheduleError("--latency must be at least 1")

    if mode in ("asap", "alap") and has_limits:
        warnings.append(f"resource limits are ignored in {mode} mode (use c{mode})")
    if mode in ("asap", "casap") and latency is not None:
        warnings.append(f"--latency is ignored in {mode} mode")
    if mode in ("casap", "calap") and not has_limits:
        warnings.append(f"no resource limits given, so {mode} matches {mode[1:]}")
    if mode in CONSTRAINED:
        _check_limits(dag, limits)

    if mode == "asap":
        steps, held = asap_steps(dag), unlimited
    elif mode == "alap":
        latency = latency if latency is not None else dag.critical_path()
        steps, held = alap_steps(dag, latency), unlimited
    elif mode == "casap":
        steps, held = casap_steps(dag, limits), limits
    elif mode == "calap":
        steps, latency = calap_steps(dag, limits, latency)
        held = limits
    else:
        steps, held, extra = custom_steps(dag, limits, latency)
        warnings += extra

    if mode in ("asap", "casap", "custom"):
        latency = max(steps.values())
    result = Schedule(mode, steps, latency, held, warnings)
    validate_schedule(dag, result)
    return result


def validate_schedule(dag: DAG, sched: Schedule) -> None:
    """Raise ScheduleError if any dependency or resource limit is broken."""
    errors = []
    missing = [str(n.dest) for n in dag.nodes if n.id not in sched.steps]
    if missing:
        errors.append(f"unscheduled: {', '.join(missing)}")
    for n in dag.nodes:
        step = sched.steps.get(n.id)
        if step is None:
            continue
        if not 1 <= step <= sched.latency:
            errors.append(f"{n.dest} is in step {step}, outside 1..{sched.latency}")
        for p in n.preds:
            pstep = sched.steps.get(p)
            if pstep is not None and pstep >= step:
                errors.append(
                    f"{n.dest} (step {step}) reads {dag.nodes[p].dest} (step {pstep}) "
                    "before it is computed"
                )
    per_step = Counter((s, dag.nodes[i].kind) for i, s in sched.steps.items())
    for (step, kind), count in sorted(per_step.items()):
        limit = sched.limits.get(kind)
        if limit is not None and count > limit:
            errors.append(f"step {step} uses {count} {UNIT_NAME[kind]}s, limit is {limit}")
    if errors:
        raise ScheduleError(f"invalid {sched.mode} schedule:\n  " + "\n  ".join(errors))


def value_lifetimes(dag: DAG, sched: Schedule) -> dict[int, tuple[int, int]]:
    """For each node, the (made, last) steps its value must be held for.

    The value is written at the end of step ``made`` and is alive across
    every step boundary k with made <= k < last. ``last`` is the step of its
    final reader. Outputs are held to the end. A value nobody reads still
    needs somewhere to land for one boundary.
    """
    spans = {}
    for n in dag.nodes:
        made = sched.steps[n.id]
        if n.is_output:
            last = sched.latency + 1
        elif n.succs:
            last = max(sched.steps[s] for s in n.succs)
        else:
            last = made + 1
        spans[n.id] = (made, last)
    return spans


def register_count(dag: DAG, sched: Schedule) -> int:
    """Registers for computed values: the most values alive across any step
    boundary. Inputs have their own dedicated registers and aren't counted.
    Left-edge register allocation always reaches exactly this count, so it
    isn't an estimate.
    """
    spans = value_lifetimes(dag, sched).values()
    return max(sum(made <= k < last for made, last in spans)
               for k in range(1, sched.latency + 1))


# --- reports -------------------------------------------------------------------

def _limits_text(limits: Limits) -> str:
    if all(v is None for v in limits.values()):
        return "none"
    return ", ".join(f"{k}={'unlimited' if v is None else v}" for k, v in limits.items())


def _hardware_text(dag: DAG, sched: Schedule) -> str:
    fus = sched.fu_counts(dag)
    parts = [f"{c} {UNIT_NAME[k]}{'s' if c != 1 else ''}" for k, c in fus.items() if c]
    regs = register_count(dag, sched)
    parts.append(f"{regs} register{'s' if regs != 1 else ''}")
    return ", ".join(parts)


def format_schedule(dag: DAG, sched: Schedule) -> str:
    critical = dag.critical_path()
    early = asap_steps(dag)
    late = alap_steps(dag, critical)
    lines = [
        f"mode: {sched.mode}    limits: {_limits_text(sched.limits)}    "
        f"latency: {sched.latency} steps",
        "",
    ]
    for step in range(1, sched.latency + 1):
        ops = [str(n) for n in sched.nodes_in_step(dag, step)] or ["(idle)"]
        lines.append(f"step {step:<3} {ops[0]}")
        lines += [f"{'':9}{op}" for op in ops[1:]]

    width = max(len(str(n)) for n in dag.nodes)
    lines += ["", f"{'operation':<{width}}  kind  ASAP  ALAP  mobility  step"]
    for n in dag.nodes:
        lines.append(
            f"{str(n):<{width}}  {n.kind:<4}  {early[n.id]:>4}  {late[n.id]:>4}  "
            f"{late[n.id] - early[n.id]:>8}  {sched.steps[n.id]:>4}"
        )
    lines += [
        f"(ASAP and ALAP here use unlimited units and the critical path, {critical} steps)",
        "",
        f"hardware: {_hardware_text(dag, sched)}",
    ]
    lines += [f"warning: {w}" for w in sched.warnings]
    return "\n".join(lines) + "\n"


def compare(dag: DAG, limits: Limits | None = None,
            latency: int | None = None) -> list[tuple[str, Schedule | str]]:
    """Schedule every mode with the same settings. A mode that can't meet
    them gets its error message in place of a schedule."""
    rows = []
    for mode in MODES:
        try:
            rows.append((mode, schedule(dag, mode, limits, latency)))
        except ScheduleError as exc:
            rows.append((mode, str(exc)))
    return rows


def format_compare(dag: DAG, rows: list[tuple[str, Schedule | str]],
                   mux_inputs=None) -> str:
    """``mux_inputs``, if given, maps a Schedule to its total mux inputs and
    adds a column. The scheduler can't compute it itself because muxes come
    from binding."""
    lines = [f"{'mode':<7} {'latency':>7} {'add':>4} {'sub':>4} {'mul':>4} "
             f"{'FUs':>4} {'regs':>5}" + (f" {'mux in':>6}" if mux_inputs else "")]
    for mode, result in rows:
        if isinstance(result, str):
            lines.append(f"{mode:<7} error: {result}")
            continue
        fus = result.fu_counts(dag)
        lines.append(
            f"{mode:<7} {result.latency:>7} {fus['add']:>4} {fus['sub']:>4} "
            f"{fus['mul']:>4} {sum(fus.values()):>4} {register_count(dag, result):>5}"
            + (f" {mux_inputs(result):>6}" if mux_inputs else "")
        )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    ap = argparse.ArgumentParser(prog="python -m hls.scheduler",
                                 description="Schedule an equation file.")
    ap.add_argument("equations")
    ap.add_argument("--mode", choices=MODES, default="asap")
    ap.add_argument("--add", type=int, metavar="N", help="max adders (constrained modes)")
    ap.add_argument("--sub", type=int, metavar="N", help="max subtractors (constrained modes)")
    ap.add_argument("--mul", type=int, metavar="N", help="max multipliers (constrained modes)")
    ap.add_argument("--latency", type=int, metavar="L",
                    help="step bound for alap, calap and custom")
    ap.add_argument("--outputs", help="comma-separated variables to keep as outputs")
    ap.add_argument("--const", metavar="NAME=VALUE,...", help="hardcode inputs, e.g. C=5,D=3")
    ap.add_argument("--compare", action="store_true",
                    help="schedule all five modes and compare their hardware")
    args = ap.parse_args()

    try:
        outputs = [n.strip() for n in args.outputs.split(",")] if args.outputs else None
        constants = parse_const_arg(args.const) if args.const else None
        dag = build_dag(to_ssa(parse_file(args.equations), outputs, constants))
        limits = {"add": args.add, "sub": args.sub, "mul": args.mul}
        if args.compare:
            print(format_compare(dag, compare(dag, limits, args.latency)), end="")
        else:
            print(format_schedule(dag, schedule(dag, args.mode, limits, args.latency)), end="")
    except (ParseError, SSAError, DAGError, ScheduleError) as exc:
        sys.exit(str(exc))
