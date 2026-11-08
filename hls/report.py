"""Human-readable reports: binding summary, switch map and board trace."""

from hls import INPUT_WIDTH
from hls.dag import UNIT_NAME
from hls.design import LED_FUS, Design
from hls.golden import evaluate
from hls.verilog_gen import debug_items


def format_binding(d: Design) -> str:
    b = d.binding
    lines = ["functional units:"]
    for fi, fu in enumerate(b.fus):
        ops = sorted((n for n in d.dag.nodes if b.op_fu[n.id] == fi),
                     key=lambda n: d.sched.steps[n.id])
        lines.append(f"  {fu.name:<6} " + "; ".join(f"step {d.sched.steps[n.id]}: {n}" for n in ops))
    lines.append("registers:")
    for r in b.regs:
        held = ", ".join(
            f"{d.dag.nodes[v].dest} (steps {b.lifetimes[v][0]}-"
            f"{'end' if b.lifetimes[v][1] > d.sched.latency else b.lifetimes[v][1]})"
            for v in r.values)
        lines.append(f"  {r.name:<6} {held}")
    lines.append("muxes:")
    muxes = []
    for fu in b.fus:
        for port, sources in (("a", fu.a_sources), ("b", fu.b_sources)):
            if len(sources) > 1:
                muxes.append(f"  {fu.name}.{port:<4} {len(sources)}:1 from "
                             + ", ".join(s.label for s in sources))
    for r in b.regs:
        if len(r.writers) > 1:
            muxes.append(f"  {r.name:<6} {len(r.writers)}:1 from "
                         + ", ".join(b.fus[fi].name for fi in r.writers))
    lines += muxes or ["  (none)"]
    lines.append(f"total mux inputs: {b.mux_inputs()}")
    return "\n".join(lines) + "\n"


def format_io_map(d: Design) -> str:
    fus, outputs, regs, inputs = debug_items(d)
    lines = [
        f"Switch map for {d.source} ({d.sched.mode} schedule)",
        "",
        "SW[15:14] picks the view, SW[13:8] the index within it, SW[7:0] the value to load.",
        "Display: digits 7-6 = FSM state, 5-4 = index, 3-0 = selected value (hex).",
        f"LED15 = done, LED13-12 = view, LED{LED_FUS - 1}-0 = which functional units are busy.",
        "Buttons: BTNC = step, BTNU = load SW[7:0] into the selected input (input view only),",
        "         BTNL = restart (back to IDLE, inputs kept), CPU_RESET = clear everything.",
        "",
        "View 00 (SW[15:14] = 00): functional unit outputs",
    ]
    lines += [f"  index {i:>2}  {label}" for i, (_, label) in enumerate(fus)]
    if len(fus) > LED_FUS:
        lines.append(f"  (units past index {LED_FUS - 1} have no busy LED)")
    lines.append("View 01: outputs (final once LED15 is on)")
    lines += [f"  index {i:>2}  {label}, held in {sig}" for i, (sig, label) in enumerate(outputs)]
    lines.append("View 10: registers")
    lines += [f"  index {i:>2}  {sig}: {label}" for i, (sig, label) in enumerate(regs)]
    lines.append("View 11: inputs (press BTNU to load SW[7:0] into the selected one)")
    lines += [f"  index {i:>2}  {label}" for i, (_, label) in enumerate(inputs)] or ["  (none)"]
    if d.program.constants:
        lines += ["", "Hardcoded constants: "
                  + ", ".join(f"{k} = {v}" for k, v in d.program.constants.items())]
    return "\n".join(lines) + "\n"


def _hex(value: int) -> str:
    return f"0x{value:04X} ({value})"


def format_trace(d: Design, vector: dict[str, int]) -> str:
    b = d.binding
    values, warnings = evaluate(d.program, vector)
    text = ", ".join(f"{k}={v}" for k, v in vector.items()) or "no switch inputs"
    lines = [f"Expected board behavior for {d.source} ({d.sched.mode}) with {text}", ""]
    lines += [f"warning: {w}" for w in warnings] + ([""] if warnings else [])
    lines += ["Setup", "  1. Press CPU_RESET."]
    if b.inputs:
        lines.append("  2. Set SW[15:14] = 11 (input view). For each input, set SW[13:8] to its")
        lines.append(f"     index and SW[{INPUT_WIDTH - 1}:0] to its value, then press BTNU:")
        for i, v in enumerate(b.inputs):
            n = vector[v.base]
            lines.append(f"       index {i:>2}  {v.base} = {n:<3}  SW[13:8] = {i:06b}  "
                         f"SW[{INPUT_WIDTH - 1}:0] = {n:0{INPUT_WIDTH}b}")
    lines += ["  3. Press BTNC once to leave IDLE (state 00 -> 01).", "",
              "Each FU output is visible BEFORE you press BTNC. The press stores it.", ""]

    for st in d.states:
        nodes = d.sched.nodes_in_step(d.dag, st.step)
        lines.append(f"State {st.step:02X} (step {st.step})")
        if not nodes:
            lines.append("  idle: nothing to check, press BTNC")
        for n in nodes:
            fi = b.op_fu[n.id]
            lhs, rhs = (x if isinstance(x, int) else values[x] for x in (n.stmt.lhs, n.stmt.rhs))
            lines.append(f"  view 00 index {fi:>2}  {b.fus[fi].name} = {_hex(values[n.dest])}"
                         f"   {n.dest} = {lhs} {n.stmt.op} {rhs}")
        if nodes:
            stores = ", ".join(f"r{b.value_reg[n.id]} <- {values[n.dest]} ({n.dest})" for n in nodes)
            lines.append(f"  press BTNC: {stores}")
        lines.append("")

    lines.append(f"State {d.done_state:02X} = DONE (LED15 on). Outputs, view 01:")
    for i, v in enumerate(d.program.outputs):
        lines.append(f"  index {i:>2}  {v.base} = {_hex(values[v])}")
    return "\n".join(lines) + "\n"
