"""Bind a schedule to hardware: functional units, registers and muxes.

* Functional units: each kind gets as many instances as its busiest step
  needs. Within a step, each op goes to the free instance that already reads
  the most of its sources, so the port muxes stay small. For + and *, the
  operands may be swapped if that reuses more sources.
* Registers: every computed value gets a register for its lifetime, and
  values whose lifetimes don't overlap share one (left-edge algorithm).
  Inputs get their own dedicated registers instead, so they stay stable.
* Muxes: each FU input port gets a mux over its distinct sources, and each
  register gets a mux over the FUs that write it. A single source needs no
  mux.
"""

from dataclasses import dataclass, field

from hls import DATA_WIDTH
from hls.dag import DAG, KINDS
from hls.scheduler import Schedule, value_lifetimes
from hls.ssa import Value

COMMUTATIVE = ("add", "mul")


class BindingError(ValueError):
    pass


@dataclass(frozen=True)
class Source:
    """Something an FU input port can read."""

    kind: str    # "reg", "in" or "const"
    name: str    # Verilog expression: r0, in_A, 16'd5
    label: str   # for reports: r0, A, 5


@dataclass
class FU:
    kind: str
    index: int
    a_sources: list[Source] = field(default_factory=list)
    b_sources: list[Source] = field(default_factory=list)

    @property
    def name(self) -> str:
        return f"{self.kind}{self.index}"


@dataclass
class Register:
    index: int
    values: list[int] = field(default_factory=list)    # node ids, in write order
    writers: list[int] = field(default_factory=list)   # FU indices feeding it

    @property
    def name(self) -> str:
        return f"r{self.index}"


@dataclass
class Binding:
    fus: list[FU]
    regs: list[Register]
    inputs: list[Value]
    op_fu: dict[int, int]                          # node id -> FU index
    op_ports: dict[int, tuple[Source, Source]]     # node id -> (port a, port b)
    value_reg: dict[int, int]                      # node id -> register index
    lifetimes: dict[int, tuple[int, int]]          # node id -> (made, last)

    def mux_inputs(self) -> int:
        """Total inputs across all muxes."""
        sizes = ([len(f.a_sources) for f in self.fus] + [len(f.b_sources) for f in self.fus]
                 + [len(r.writers) for r in self.regs])
        return sum(n for n in sizes if n > 1)


def bind(dag: DAG, sched: Schedule) -> Binding:
    lifetimes = value_lifetimes(dag, sched)

    # Left-edge: take values in order of when they're made, and put each in
    # the lowest-numbered register that's free by then.
    regs: list[Register] = []
    free_from: list[int] = []
    value_reg: dict[int, int] = {}
    for nid in sorted(lifetimes, key=lambda i: (lifetimes[i][0], i)):
        made, last = lifetimes[nid]
        r = next((i for i, f in enumerate(free_from) if f <= made), None)
        if r is None:
            r = len(regs)
            regs.append(Register(r))
            free_from.append(0)
        regs[r].values.append(nid)
        free_from[r] = last
        value_reg[nid] = r

    producer = {n.dest: n.id for n in dag.nodes}

    def source_of(operand: Value | int) -> Source:
        if isinstance(operand, int):
            return Source("const", f"{DATA_WIDTH}'d{operand}", str(operand))
        if operand in producer:
            reg = regs[value_reg[producer[operand]]]
            return Source("reg", reg.name, reg.name)
        return Source("in", f"in_{operand.base}", operand.base)

    counts = sched.fu_counts(dag)
    fus = [FU(kind, i) for kind in KINDS for i in range(counts[kind])]
    op_fu: dict[int, int] = {}
    op_ports: dict[int, tuple[Source, Source]] = {}

    for step in range(1, sched.latency + 1):
        busy: set[int] = set()
        for n in sched.nodes_in_step(dag, step):
            a, b = source_of(n.stmt.lhs), source_of(n.stmt.rhs)
            options = [(a, b)]
            if n.kind in COMMUTATIVE and a != b:
                options.append((b, a))
            out = regs[value_reg[n.id]]
            best = None
            for fi, fu in enumerate(fus):
                if fu.kind != n.kind or fi in busy:
                    continue
                for pa, pb in options:
                    score = (pa in fu.a_sources) + (pb in fu.b_sources) + (fi in out.writers)
                    if best is None or score > best[0]:
                        best = (score, fi, pa, pb)
            _, fi, pa, pb = best
            busy.add(fi)
            fu = fus[fi]
            if pa not in fu.a_sources:
                fu.a_sources.append(pa)
            if pb not in fu.b_sources:
                fu.b_sources.append(pb)
            if fi not in out.writers:
                out.writers.append(fi)
            op_fu[n.id] = fi
            op_ports[n.id] = (pa, pb)

    binding = Binding(fus, regs, list(dag.inputs), op_fu, op_ports, value_reg, lifetimes)
    validate_binding(dag, sched, binding)
    return binding


def validate_binding(dag: DAG, sched: Schedule, binding: Binding) -> None:
    """Raise BindingError if two ops share a unit in one step, or two live
    values share a register."""
    errors = []
    seen: dict[tuple[int, int], int] = {}
    for n in dag.nodes:
        fi = binding.op_fu[n.id]
        if binding.fus[fi].kind != n.kind:
            errors.append(f"{n.dest} ({n.kind}) is bound to {binding.fus[fi].name}")
        key = (sched.steps[n.id], fi)
        if key in seen:
            errors.append(f"{n.dest} and {dag.nodes[seen[key]].dest} both use "
                          f"{binding.fus[fi].name} in step {key[0]}")
        seen[key] = n.id
    for reg in binding.regs:
        spans = sorted(binding.lifetimes[v] for v in reg.values)
        for (_, last), (made, _) in zip(spans, spans[1:]):
            if made < last:
                errors.append(f"{reg.name} holds two live values at once")
    if errors:
        raise BindingError("invalid binding:\n  " + "\n  ".join(errors))
