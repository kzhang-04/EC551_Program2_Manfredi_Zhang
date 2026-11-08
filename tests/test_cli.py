from pathlib import Path

import pytest

from hls.cli import main

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
PDF = str(EXAMPLES / "pdf_example.txt")


def test_writes_every_file(tmp_path, capsys):
    assert main([PDF, "--out", str(tmp_path), "--export-ssa"]) == 0
    names = {p.name for p in tmp_path.iterdir()}
    assert names == {"hls_datapath.v", "hls_controller.v", "hls_core.v", "testbench.v",
                     "schedule.txt", "dag.dot", "io_map.txt", "trace.txt", "ssa.txt"}
    assert "wrote" in capsys.readouterr().out


def test_first_test_vector_drives_the_trace(tmp_path, capsys):
    main([PDF, "--out", str(tmp_path), "--test", "A=10,B=4"])
    assert "with A=10, B=4" in (tmp_path / "trace.txt").read_text(encoding="utf-8")


def test_compare_includes_mux_column(capsys):
    assert main([PDF, "--compare"]) == 0
    header = capsys.readouterr().out.splitlines()[0]
    assert header.split()[-2:] == ["mux", "in"]


@pytest.mark.parametrize("args, message", [
    (["--test", "A=3"], "missing B"),
    (["--test", "A=3,B=300"], "won't fit"),
    (["--mode", "casap", "--mul", "0"], "allows no multipliers"),
    (["--const", "Q=1"], "only inputs can be hardcoded"),
    (["--outputs", "W"], "never assigned"),
])
def test_errors_are_clean(tmp_path, capsys, args, message):
    assert main([PDF, "--out", str(tmp_path), *args]) == 1
    err = capsys.readouterr().err
    assert err.startswith("error:") and message in err
    assert "Traceback" not in err


def test_bad_equation_file(tmp_path, capsys):
    bad = tmp_path / "bad.txt"
    bad.write_text("X = A / B\n", encoding="utf-8")
    assert main([str(bad), "--out", str(tmp_path / "out")]) == 1
    assert "unexpected character" in capsys.readouterr().err
