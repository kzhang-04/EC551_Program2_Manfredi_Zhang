"""Command line: equations in, Verilog and reports out.

    python -m hls examples/pdf_example.txt --mode casap --add 1 --test A=3,B=2 --sim

Writes into generated/<name>_<mode>/ unless --out says otherwise:

    hls_datapath.v, hls_controller.v, hls_core.v   the synthesized design
    testbench.v                                    self-checking simulation
    schedule.txt                                   schedule and binding
    dag.dot                                        dependency graph (Graphviz)
    io_map.txt                                     what each switch setting shows
    trace.txt                                      expected display after each press
    ssa.txt                                        only with --export-ssa
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from hls.binding import BindingError
from hls.dag import DAGError, to_dot
from hls.design import DesignError, build_design, format_full_compare
from hls.golden import GoldenError, check_vector, random_vectors
from hls.parser import ParseError, parse_file
from hls.report import format_binding, format_io_map, format_trace
from hls.scheduler import MODES, ScheduleError, format_schedule
from hls.ssa import SSAError, export_ssa, parse_const_arg, to_ssa
from hls.tb_gen import generate_testbench
from hls.verilog_gen import generate_controller, generate_core, generate_datapath

ROOT = Path(__file__).resolve().parent.parent
PRIMITIVES = [ROOT / "rtl" / f for f in ("adder.v", "subtractor.v", "multiplier.v")]
DESIGN_FILES = ["hls_datapath.v", "hls_controller.v", "hls_core.v"]

ERRORS = (ParseError, SSAError, DAGError, ScheduleError, BindingError, DesignError, GoldenError)


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m hls",
                                 description="Synthesize an equation file into Verilog.")
    ap.add_argument("equations")
    ap.add_argument("--mode", choices=MODES, default="asap")
    ap.add_argument("--add", type=int, metavar="N", help="max adders (constrained modes)")
    ap.add_argument("--sub", type=int, metavar="N", help="max subtractors (constrained modes)")
    ap.add_argument("--mul", type=int, metavar="N", help="max multipliers (constrained modes)")
    ap.add_argument("--latency", type=int, metavar="L",
                    help="step bound for alap, calap and custom")
    ap.add_argument("--outputs", help="comma-separated variables to keep as outputs")
    ap.add_argument("--const", metavar="NAME=VALUE,...", help="hardcode inputs, e.g. C=5,D=3")
    ap.add_argument("--test", action="append", default=[], metavar="NAME=VALUE,...",
                    help="input values to test (repeatable); the first one drives trace.txt")
    ap.add_argument("--random-tests", type=int, default=4, metavar="N",
                    help="extra random test vectors (default 4)")
    ap.add_argument("--out", metavar="DIR", help="output folder (default generated/<name>_<mode>)")
    ap.add_argument("--export-ssa", nargs="?", const="", metavar="PATH",
                    help="also write the SSA form (default <out>/ssa.txt)")
    ap.add_argument("--compare", action="store_true",
                    help="only print a comparison of all five modes")
    ap.add_argument("--sim", action="store_true", help="run the testbench with Icarus Verilog")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        outputs = [n.strip() for n in args.outputs.split(",")] if args.outputs else None
        constants = parse_const_arg(args.const) if args.const else None
        limits = {"add": args.add, "sub": args.sub, "mul": args.mul}
        program = to_ssa(parse_file(args.equations), outputs, constants)

        if args.compare:
            print(format_full_compare(program, limits, args.latency), end="")
            return 0

        out = Path(args.out or ROOT / "generated" / f"{Path(args.equations).stem}_{args.mode}")
        design = build_design(program, args.mode, limits, args.latency,
                              source=Path(args.equations).name)
        vectors = [parse_const_arg(t, "--test") for t in args.test]
        for v in vectors:
            check_vector(program, v)
        vectors += random_vectors(program, max(0, args.random_tests))
        if not vectors:
            vectors = [{}]
    except ERRORS as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    out.mkdir(parents=True, exist_ok=True)
    files = {
        "hls_datapath.v": generate_datapath(design),
        "hls_controller.v": generate_controller(design),
        "hls_core.v": generate_core(design),
        "testbench.v": generate_testbench(design, vectors),
        "schedule.txt": format_schedule(design.dag, design.sched) + "\n" + format_binding(design),
        "dag.dot": to_dot(design.dag),
        "io_map.txt": format_io_map(design),
        "trace.txt": format_trace(design, vectors[0]),
    }
    for name, text in files.items():
        (out / name).write_text(text, encoding="utf-8")
    written = list(files)
    if args.export_ssa is not None:
        export_ssa(program, args.export_ssa or out / "ssa.txt", args.equations)
        written.append(args.export_ssa or "ssa.txt")

    print(format_schedule(design.dag, design.sched), end="")
    print(format_binding(design), end="")
    print(f"\nwrote {out}: {', '.join(map(str, written))}")
    return run_sim(out) if args.sim else 0


def run_sim(out: Path) -> int:
    """Compile and run testbench.v with Icarus Verilog. Returns 0 on PASS."""
    if shutil.which("iverilog") is None:
        print("error: --sim needs Icarus Verilog (iverilog) on PATH", file=sys.stderr)
        return 1
    vvp = out / "sim.vvp"
    sources = [out / "testbench.v", *(out / f for f in DESIGN_FILES), *PRIMITIVES]
    compiled = subprocess.run(["iverilog", "-o", str(vvp), *map(str, sources)],
                              capture_output=True, text=True)
    if compiled.returncode != 0:
        print(compiled.stdout + compiled.stderr, file=sys.stderr)
        print("error: the generated Verilog didn't compile", file=sys.stderr)
        return 1
    result = subprocess.run(["vvp", "-n", str(vvp)], capture_output=True, text=True)
    report = [line for line in result.stdout.splitlines() if not line.endswith("$finish called at")
              and "$finish called" not in line]
    print("\nsimulation:\n  " + "\n  ".join(report))
    return 0 if any(line.startswith("PASS:") for line in report) else 1
