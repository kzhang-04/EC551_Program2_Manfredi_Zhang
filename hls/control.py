"""Turn a binding into the controller's per-state signal table.

State k runs schedule step k. In each state the controller drives:

* ``<fu>_a_sel`` / ``<fu>_b_sel``: which source each busy FU port reads.
* ``<reg>_we``: write this register on the clock edge that leaves the state.
* ``<reg>_sel``: which FU that register stores.

Signals not listed for a state are 0. A port or register with only one
source has no select signal at all.
"""

from dataclasses import dataclass

from hls.binding import Binding
from hls.dag import DAG
from hls.scheduler import Schedule


@dataclass(frozen=True)
class Signal:
    name: str
    width: int


@dataclass
class StateControl:
    step: int
    values: dict[str, int]   # signal name -> value in this state
    active: list[int]        # FU indices busy in this state
    notes: list[str]         # one line per op, for comments and reports


def sel_width(n: int) -> int:
    """Bits needed to choose between n sources."""
    return max(1, (n - 1).bit_length())


def control_signals(binding: Binding) -> list[Signal]:
    signals = []
    for fu in binding.fus:
        for port, sources in (("a", fu.a_sources), ("b", fu.b_sources)):
            if len(sources) > 1:
                signals.append(Signal(f"{fu.name}_{port}_sel", sel_width(len(sources))))
    for reg in binding.regs:
        signals.append(Signal(f"{reg.name}_we", 1))
        if len(reg.writers) > 1:
            signals.append(Signal(f"{reg.name}_sel", sel_width(len(reg.writers))))
    return signals


def build_control(dag: DAG, sched: Schedule, binding: Binding) -> list[StateControl]:
    """One StateControl per schedule step, in step order."""
    states = []
    for step in range(1, sched.latency + 1):
        values: dict[str, int] = {}
        active, notes = [], []
        for n in sched.nodes_in_step(dag, step):
            fi = binding.op_fu[n.id]
            fu = binding.fus[fi]
            a, b = binding.op_ports[n.id]
            if len(fu.a_sources) > 1:
                values[f"{fu.name}_a_sel"] = fu.a_sources.index(a)
            if len(fu.b_sources) > 1:
                values[f"{fu.name}_b_sel"] = fu.b_sources.index(b)
            reg = binding.regs[binding.value_reg[n.id]]
            values[f"{reg.name}_we"] = 1
            if len(reg.writers) > 1:
                values[f"{reg.name}_sel"] = reg.writers.index(fi)
            active.append(fi)
            notes.append(f"{fu.name} = {a.label} {n.stmt.op} {b.label} -> {reg.name}  ({n})")
        states.append(StateControl(step, values, sorted(active), notes))
    return states
