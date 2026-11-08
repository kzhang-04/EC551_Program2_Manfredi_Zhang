"""Build the data-dependency DAG for an SSA program.

Each SSA statement becomes one node, an arithmetic operation. An edge from
node P to node N means N reads the value P produces, so N must run in a later
step than P. Primary inputs and literals are not nodes: they are available
before step 1.
"""

import argparse
import heapq
import sys
from dataclasses import dataclass, field
from pathlib import Path

from hls.parser import ParseError, parse_file
from hls.ssa import SSAError, SSAProgram, SSAStatement, Value, parse_const_arg, to_ssa

KINDS = ("add", "sub", "mul")
KIND_OF_OP = {"+": "add", "-": "sub", "*": "mul"}
UNIT_NAME = {"add": "adder", "sub": "subtractor", "mul": "multiplier"}


class DAGError(ValueError):
    pass


@dataclass
class Node:
    id: int
    stmt: SSAStatement
    is_output: bool
    preds: list[int] = field(default_factory=list)   # nodes this one reads
    succs: list[int] = field(default_factory=list)   # nodes that read this one

    @property
    def kind(self) -> str:
        return KIND_OF_OP[self.stmt.op]

    @property
    def dest(self) -> Value:
        return self.stmt.dest

    def __str__(self) -> str:
        return str(self.stmt)


@dataclass
class DAG:
    nodes: list[Node]
    inputs: list[Value]
    outputs: list[Value]

    def topo_order(self) -> list[Node]:
        """Kahn's algorithm, lowest node id first among ready nodes.

        SSA can't produce a cycle, so a leftover node means a bug upstream.
        """
        waiting = {n.id: len(n.preds) for n in self.nodes}
        ready = [n.id for n in self.nodes if not n.preds]
        heapq.heapify(ready)
        order = []
        while ready:
            node = self.nodes[heapq.heappop(ready)]
            order.append(node)
            for s in node.succs:
                waiting[s] -= 1
                if waiting[s] == 0:
                    heapq.heappush(ready, s)
        if len(order) != len(self.nodes):
            stuck = [str(self.nodes[i].dest) for i, w in waiting.items() if w]
            raise DAGError(f"dependency cycle through {', '.join(stuck)}")
        return order

    def kind_counts(self) -> dict[str, int]:
        return {k: sum(n.kind == k for n in self.nodes) for k in KINDS}

    def critical_path(self) -> int:
        """Number of steps on the longest dependency chain."""
        depth: dict[int, int] = {}
        for n in self.topo_order():
            depth[n.id] = 1 + max((depth[p] for p in n.preds), default=0)
        return max(depth.values())


def build_dag(program: SSAProgram) -> DAG:
    producer = {s.dest: i for i, s in enumerate(program.statements)}
    outputs = set(program.outputs)
    nodes = [Node(i, s, s.dest in outputs) for i, s in enumerate(program.statements)]
    for node in nodes:
        # dict.fromkeys drops the duplicate edge in X + X.
        for v in dict.fromkeys(node.stmt.sources):
            if v in producer:
                node.preds.append(producer[v])
                nodes[producer[v]].succs.append(node.id)
    dag = DAG(nodes, list(program.inputs), list(program.outputs))
    dag.topo_order()
    return dag


def format_dag(dag: DAG) -> str:
    def ids(xs):
        return ", ".join(f"n{x}" for x in xs) or "-"

    width = max(len(str(n)) for n in dag.nodes)
    lines = [
        f"inputs:  {', '.join(map(str, dag.inputs)) or '(none)'}",
        f"outputs: {', '.join(map(str, dag.outputs))}",
        f"critical path: {dag.critical_path()} steps",
        "",
        f"{'node':<5} {'operation':<{width}}  {'after':<12} feeds",
    ]
    for n in dag.nodes:
        tag = "  (output)" if n.is_output else ""
        lines.append(f"n{n.id:<4} {str(n):<{width}}  {ids(n.preds):<12} {ids(n.succs)}{tag}")
    return "\n".join(lines) + "\n"


def to_dot(dag: DAG) -> str:
    """Graphviz source: boxes are inputs, circles are operations, and double
    circles are outputs."""
    producer = {n.dest: n.id for n in dag.nodes}
    lines = ["digraph dag {", '  node [fontname="Helvetica"];']
    lines += [f'  "{v}" [shape=box];' for v in dag.inputs]
    for n in dag.nodes:
        rings = 2 if n.is_output else 1
        lines.append(f'  n{n.id} [label="{n.dest}\\n{n.stmt.op}", shape=circle, peripheries={rings}];')
    for n in dag.nodes:
        for side, operand in (("l", n.stmt.lhs), ("r", n.stmt.rhs)):
            if isinstance(operand, int):
                lines.append(f'  c{n.id}{side} [label="{operand}", shape=plaintext];')
                src = f"c{n.id}{side}"
            elif operand in producer:
                src = f"n{producer[operand]}"
            else:
                src = f'"{operand}"'
            lines.append(f"  {src} -> n{n.id};")
    lines.append("}")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    ap = argparse.ArgumentParser(prog="python -m hls.dag",
                                 description="Print the dependency graph of an equation file.")
    ap.add_argument("equations")
    ap.add_argument("--outputs", help="comma-separated variables to keep as outputs")
    ap.add_argument("--const", metavar="NAME=VALUE,...", help="hardcode inputs, e.g. C=5,D=3")
    ap.add_argument("--dot", metavar="PATH", help="also write Graphviz source to PATH")
    args = ap.parse_args()

    try:
        outputs = [n.strip() for n in args.outputs.split(",")] if args.outputs else None
        constants = parse_const_arg(args.const) if args.const else None
        dag = build_dag(to_ssa(parse_file(args.equations), outputs, constants))
    except (ParseError, SSAError, DAGError) as exc:
        sys.exit(str(exc))

    print(format_dag(dag), end="")
    if args.dot:
        path = Path(args.dot)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(to_dot(dag), encoding="utf-8")
        print(f"\nwrote {path}")
