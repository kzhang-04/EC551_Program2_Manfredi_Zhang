"""Bundle every stage's result into one Design for the generators."""

from dataclasses import dataclass

from hls.binding import Binding, bind
from hls.control import Signal, StateControl, build_control, control_signals
from hls.dag import DAG, build_dag
from hls.scheduler import Limits, Schedule, compare, format_compare, schedule
from hls.ssa import SSAProgram

# Limits set by the fixed hls_core ports: 8-bit state, 6-bit dbg_sel/in_sel.
MAX_STATES = 255
MAX_INDEX = 64
LED_FUS = 12


class DesignError(ValueError):
    pass


@dataclass
class Design:
    source: str
    program: SSAProgram
    dag: DAG
    sched: Schedule
    binding: Binding
    signals: list[Signal]
    states: list[StateControl]

    @property
    def done_state(self) -> int:
        return self.sched.latency + 1

    def output_reg(self, index: int) -> int:
        """Register holding output number ``index`` at the end."""
        producer = {n.dest: n.id for n in self.dag.nodes}
        return self.binding.value_reg[producer[self.program.outputs[index]]]


def build_design(program: SSAProgram, mode: str, limits: Limits | None = None,
                 latency: int | None = None, source: str = "<input>") -> Design:
    dag = build_dag(program)
    sched = schedule(dag, mode, limits, latency)
    if sched.latency + 1 > MAX_STATES:
        raise DesignError(f"latency {sched.latency} needs more than {MAX_STATES} FSM states")
    binding = bind(dag, sched)
    for what, count in (("inputs", len(binding.inputs)), ("functional units", len(binding.fus)),
                        ("registers", len(binding.regs)), ("outputs", len(program.outputs))):
        if count > MAX_INDEX:
            raise DesignError(f"{count} {what} won't fit the 6-bit switch index (max {MAX_INDEX})")
    return Design(source, program, dag, sched, binding,
                  control_signals(binding), build_control(dag, sched, binding))


def format_full_compare(program: SSAProgram, limits: Limits | None = None,
                        latency: int | None = None) -> str:
    """The scheduler's comparison table plus a mux-input column."""
    dag = build_dag(program)
    return format_compare(dag, compare(dag, limits, latency),
                          mux_inputs=lambda s: bind(dag, s).mux_inputs())
