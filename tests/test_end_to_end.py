"""Generate Verilog with the full tool and simulate it with Icarus Verilog.

Each case runs ``python -m hls ... --sim``, which only succeeds if the
generated testbench prints PASS: every FU output, register write and final
output matched the Python reference model.
"""

import random
import shutil
from pathlib import Path

import pytest

from hls.cli import main
from tests.test_scheduler import random_equations

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"

pytestmark = pytest.mark.skipif(shutil.which("iverilog") is None,
                                reason="Icarus Verilog (iverilog) is not on PATH")

MODE_ARGS = {
    "asap": ["--mode", "asap"],
    "alap": ["--mode", "alap"],
    "casap": ["--mode", "casap", "--add", "1", "--sub", "1", "--mul", "1"],
    "calap": ["--mode", "calap", "--add", "1", "--sub", "1", "--mul", "1"],
    "custom": ["--mode", "custom"],
}


def synthesize_and_simulate(tmp_path, equations, extra, capsys):
    code = main([str(equations), "--out", str(tmp_path), "--sim", *extra])
    output = capsys.readouterr()
    assert code == 0, output.out + output.err
    assert "PASS:" in output.out


@pytest.mark.parametrize("mode", MODE_ARGS)
@pytest.mark.parametrize("example", ["pdf_example.txt", "reassign.txt", "parallel.txt"])
def test_examples(tmp_path, capsys, example, mode):
    synthesize_and_simulate(tmp_path, EXAMPLES / example, MODE_ARGS[mode], capsys)


@pytest.mark.parametrize("extra", [
    ["--outputs", "Y,Q", "--test", "A=3,B=2"],          # register sharing
    ["--mode", "alap", "--latency", "6"],               # idle states at the start
    ["--mode", "custom", "--latency", "5"],             # fewer units, longer schedule
    ["--const", "B=200", "--test", "A=255"],            # hardcoded input, overflow
])
def test_variations(tmp_path, capsys, extra):
    synthesize_and_simulate(tmp_path, EXAMPLES / "pdf_example.txt", extra, capsys)


def test_all_inputs_hardcoded(tmp_path, capsys):
    synthesize_and_simulate(tmp_path, EXAMPLES / "pdf_example.txt",
                            ["--const", "A=7,B=9"], capsys)


@pytest.mark.parametrize("seed", range(8))
@pytest.mark.parametrize("mode", MODE_ARGS)
def test_random_programs(tmp_path, capsys, seed, mode):
    equations = tmp_path / "random.txt"
    equations.write_text(random_equations(random.Random(seed)), encoding="utf-8")
    synthesize_and_simulate(tmp_path / "out", equations, MODE_ARGS[mode], capsys)
