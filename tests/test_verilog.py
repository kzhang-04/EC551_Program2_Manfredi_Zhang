"""Run the Verilog testbenches with Icarus Verilog, if it's installed."""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(shutil.which("iverilog") is None,
                                reason="Icarus Verilog (iverilog) is not on PATH")


def run_testbench(tmp_path, sources):
    out = tmp_path / "sim.vvp"
    # -Wno-timescale: that warning crashes Icarus 13 on Windows (segfault).
    subprocess.run(["iverilog", "-Wall", "-Wno-timescale", "-o", str(out), *map(str, sources)],
                   check=True, capture_output=True, text=True, cwd=ROOT)
    return subprocess.run(["vvp", "-n", str(out)], check=True, capture_output=True,
                          text=True, cwd=ROOT).stdout


def test_primitives(tmp_path):
    output = run_testbench(tmp_path, [
        ROOT / "tb" / "tb_primitives.v",
        ROOT / "rtl" / "adder.v",
        ROOT / "rtl" / "subtractor.v",
        ROOT / "rtl" / "multiplier.v",
    ])
    assert "PASS: tb_primitives" in output, output


def test_top_nexys(tmp_path):
    from hls.cli import main
    core = tmp_path / "core"
    assert main([str(ROOT / "examples" / "pdf_example.txt"), "--out", str(core)]) == 0
    output = run_testbench(tmp_path, [
        ROOT / "tb" / "tb_top_nexys.v",
        *(ROOT / "rtl").glob("*.v"),
        *core.glob("hls_*.v"),
    ])
    assert "PASS: tb_top_nexys" in output, output
