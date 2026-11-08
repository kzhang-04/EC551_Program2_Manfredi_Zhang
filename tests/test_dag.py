from pathlib import Path

import pytest

from hls.dag import DAG, DAGError, build_dag, format_dag, to_dot
from hls.parser import parse_file, parse_text
from hls.ssa import to_ssa

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def dag_of(text, outputs=None):
    return build_dag(to_ssa(parse_text(text), outputs))


def test_pdf_example_edges():
    dag = build_dag(to_ssa(parse_file(EXAMPLES / "pdf_example.txt")))
    # X, Y, Z, Q are n0..n3
    assert [n.preds for n in dag.nodes] == [[], [0], [0], [2]]
    assert [n.succs for n in dag.nodes] == [[1, 2], [], [3], []]
    assert [n.kind for n in dag.nodes] == ["add", "add", "sub", "mul"]
    assert dag.critical_path() == 3


def test_inputs_and_literals_are_not_nodes():
    dag = dag_of("Y = A * 2")
    assert len(dag.nodes) == 1
    assert dag.nodes[0].preds == []


def test_same_value_read_twice_gives_one_edge():
    dag = dag_of("X = A + 1\nY = X + X")
    assert dag.nodes[1].preds == [0]
    assert dag.nodes[0].succs == [1]


def test_reassignment_links_to_the_right_version():
    dag = dag_of("X = A + B\nX = C + D\nY = X * 2")
    assert dag.nodes[2].preds == [1]
    assert dag.nodes[0].succs == []


def test_output_flags_follow_narrowing():
    dag = dag_of("X = A + B\nY = X * 2", outputs=["Y"])
    assert [n.is_output for n in dag.nodes] == [False, True]


def test_topo_order_respects_edges():
    dag = build_dag(to_ssa(parse_file(EXAMPLES / "parallel.txt")))
    position = {n.id: i for i, n in enumerate(dag.topo_order())}
    for n in dag.nodes:
        assert all(position[p] < position[n.id] for p in n.preds)


def test_cycle_is_detected():
    dag = dag_of("X = A + 1\nY = X + 1")
    dag.nodes[0].preds.append(1)
    dag.nodes[1].succs.append(0)
    with pytest.raises(DAGError, match="cycle"):
        dag.topo_order()


def test_kind_counts():
    dag = build_dag(to_ssa(parse_file(EXAMPLES / "parallel.txt")))
    assert dag.kind_counts() == {"add": 5, "sub": 1, "mul": 2}


def test_format_dag():
    text = format_dag(dag_of("X = A + B\nY = X * 2"))
    assert "critical path: 2 steps" in text
    assert "n0    X_1 = A_0 + B_0  -            n1  (output)" in text


def test_dot_output():
    dot = to_dot(dag_of("X = A + B\nY = X * 2", outputs=["Y"]))
    assert dot.startswith("digraph dag {")
    assert '"A_0" [shape=box];' in dot
    assert 'n1 [label="Y_1\\n*", shape=circle, peripheries=2];' in dot
    assert "n0 -> n1;" in dot
    assert 'c1r [label="2", shape=plaintext];' in dot
    assert dot.rstrip().endswith("}")
