# EC551 Program 2: Hardware Synthesis

Ethan Manfredi and Kevin Zhang, Fall 2026

A small high-level synthesis (HLS) tool written in Python. It turns a text file of arithmetic equations into a scheduled, resource-bound Verilog design (datapath + FSM controller) that runs on a Digilent Nexys A7. You step the design through its control states with a push button and pick which functional unit, register, input or output appears on the 7-segment display with the switches.

```
equations.txt ─► parse ─► SSA ─► DAG ─► schedule ─► bind ─► Verilog ─► Icarus sim ─► Vivado ─► Nexys A7
```

---

## Contents

1. [Repository layout](#repository-layout)
2. [Tool architecture](#tool-architecture)
3. [Requirements and setup](#requirements-and-setup)
4. [Running the tool](#running-the-tool)
5. [Building and programming the FPGA](#building-and-programming-the-fpga)
6. [Using the board](#using-the-board)
7. [Testing](#testing)
8. [30-minute demo plan](#30-minute-demo-plan)

---

## Repository layout

```
hls/                    the synthesis tool (python -m hls)
  parser.py               equation file -> validated statements
  ssa.py                  statements -> single static assignment form, --export-ssa
  dag.py                  SSA -> dependency DAG, Graphviz export
  scheduler.py            ASAP, constrained ASAP, ALAP, constrained ALAP, custom
  binding.py              functional units, registers (left-edge sharing), muxes
  control.py              per-state control signal table for the FSM
  verilog_gen.py          writes hls_datapath.v, hls_controller.v, hls_core.v
  tb_gen.py               writes a self-checking testbench.v
  golden.py               Python reference model the testbench checks against
  report.py               schedule/binding report, switch map, expected board trace
  design.py               bundles all stages; mode comparison table
  cli.py                  command-line interface
rtl/                    hand-written Verilog, the same for every design
  adder.v subtractor.v multiplier.v   16-bit functional unit primitives
  debouncer.v             button synchronizer + debouncer -> one-cycle pulse
  seven_seg.v             8-digit multiplexed hex display driver
  top_nexys.v             board top level: switches, buttons, LEDs, display
constraints/
  nexys_a7.xdc            pin and timing constraints for the Nexys A7
scripts/
  build.tcl               Vivado non-project flow: synth, place, route, bitstream, program
  build.ps1               one command: equations -> sim -> bitstream -> board
tb/
  tb_primitives.v         unit tests for the adder, subtractor and multiplier
  tb_top_nexys.v          board-level test: bouncing buttons, loading, stepping, views
tests/                  pytest suite (439 tests; Verilog ones need Icarus)
examples/               sample equation files
  pdf_example.txt         the example from the assignment handout
  parallel.txt            wide tree, shows the difference between scheduling modes
  reassign.txt            reassignment, exercises SSA renaming
  invalid_syntax.txt      every kind of parse error, with line numbers
generated/              committed sample output (pdf_example, ASAP)
build/                  (git-ignored) per-run tool output, bitstreams, Vivado reports
```

---

## Tool architecture

Each stage is a separate module with its own tests. Every stage's result is collected in a `Design` object (`hls/design.py`) that the generators read from.

### 1. Parsing (`parser.py`)
Each line is `DEST = LHS op RHS` with `op` one of `+ - *`. An operand can be a variable or a non-negative integer literal. `#` starts a comment and whitespace is optional. The parser reports **every** error in the file at once, with line numbers and a hint, rather than stopping at the first one. It also rejects more than 20 equations.

### 2. SSA (`ssa.py`)
Each assignment creates a new version (`X_1`, `X_2`, …), and each read refers to the newest version at that point. Version 0 (`A_0`) is a primary input. `--const C=5` hardcodes an input: it becomes a literal and is no longer loaded from the switches. `--outputs` narrows which variables are kept as outputs, which lets more registers be shared. `--export-ssa` writes the SSA form to a text file.

```
X = A + B      X_1 = A_0 + B_0
X = X * 2      X_2 = X_1 * 2
A = A + 1      A_1 = A_0 + 1
Y = X - A      Y_1 = X_2 - A_1
X = Y + X      X_3 = Y_1 + X_2
```

### 3. DAG and scheduling (`dag.py`, `scheduler.py`)
Each SSA statement becomes one DAG node. An edge means the node reads another node's result. Every operation takes one control step (one clock cycle) and must run after everything it reads.

| Mode | What it does |
|---|---|
| `asap` | Every op as early as its inputs allow. Unlimited units, so latency equals the critical path. |
| `casap` | Forward list scheduling with at most `--add/--sub/--mul` units of each kind. Priority is longest path to the end. |
| `alap` | Every op as late as possible within `--latency` (default: the critical path). Unlimited units. |
| `calap` | The same list scheduler run backward from the latency bound, with resource limits. |
| `custom` | **Fewest units that meet a latency target.** Starts each kind at its lower bound ⌈ops/latency⌉ and runs a least-slack-first list scheduler, breaking ties by register pressure (prefer the op that frees the most registers). While an op misses its ALAP deadline, it adds one unit of that op's kind and tries again. User limits act as caps. |

`--compare` prints all five modes side by side:

```
$ python -m hls examples/parallel.txt --compare --latency 6
mode    latency  add  sub  mul  FUs  regs mux in
asap          4    4    1    2    7     8      4
casap         4    4    1    2    7     8      4
alap          6    4    1    2    7     8      4
calap         6    4    1    2    7     8      4
custom        5    2    1    1    4     8     14
```

### 4. Binding: datapath construction (`binding.py`)
- **Functional units:** each kind gets as many instances as its busiest step needs. Within a step, an op goes to the free unit that already reads most of its sources, which keeps the muxes small. For `+` and `*` the operands may be swapped when that reuses more sources.
- **Registers:** sit at the cycle boundaries. Every computed value gets a register for its lifetime, and values whose lifetimes don't overlap share one (left-edge algorithm). Inputs have their own dedicated registers, so they stay stable.
- **Muxes:** each FU input port gets a mux over its distinct sources, and each register gets a mux over the FUs that write it. A port or register with a single source has no mux at all.

### 5. Controller (`control.py`, `verilog_gen.py`)
A Moore FSM: `IDLE → S1 … Sn → DONE`. State *k* runs schedule step *k*. It drives each FU port's mux select, plus each register's write-enable and input select. The FSM and all registers only advance when `en` is high. On the board, `en` is one debounced press of BTNC, so **one press = one clock step**.

### 6. Generated Verilog
| File | Contents |
|---|---|
| `hls_datapath.v` | input registers, exactly the number of `adder`/`subtractor`/`multiplier` instances the schedule needs, the muxes, the working registers, and an observability mux |
| `hls_controller.v` | the FSM and its control signal table |
| `hls_core.v` | the datapath and controller together, behind a port list that is the same for every design, so `top_nexys.v` never changes |
| `testbench.v` | self-checking testbench; checks every state, every busy FU output, every register write and every final output against the Python model |

Other files in the same folder: `schedule.txt` (schedule plus binding report), `dag.dot` (Graphviz), `io_map.txt` (what each switch setting shows), `trace.txt` (expected display after every BTNC press), and `ssa.txt`.

### Bit-width
The datapath is **16 bits** and inputs are loaded **8 bits at a time** from `SW[7:0]`. Results are shown as 4 hex digits. That covers 8-bit × 8-bit products with no overflow, and is wide enough for most chained equations. Arithmetic wraps modulo 2¹⁶ in both the hardware and the Python model, so they always agree. A 16×16 multiply maps onto one DSP48 slice.

---

## Requirements and setup

| Tool | Version used | Needed for |
|---|---|---|
| Python | 3.10+ (tested on 3.14) | the tool itself (standard library only) |
| pytest | any recent | the test suite |
| Icarus Verilog | 12.0 | `--sim`, Verilog tests |
| Vivado | 2026.1 (2024.2 also works) | bitstream and programming |

```powershell
pip install pytest
```

**Icarus Verilog on Windows (MSYS2):**
```bash
pacman -S mingw-w64-ucrt-x86_64-iverilog mingw-w64-ucrt-x86_64-bzip2
```
Add `C:\msys64\ucrt64\bin` to PATH. The bzip2 package is required: `vvp` can't load `system.vpi` without `libbz2-1.dll`, and the failure is a confusing *"`$display` is not defined"* error.

**Vivado:** add its `bin` folder (e.g. `C:\AMDDesignTools\2026.1\Vivado\bin`) to PATH. If Vivado isn't on PATH, `build.ps1` looks in the standard install locations.

---

## Running the tool

```powershell
python -m hls <equations.txt> [options]
```

| Option | Meaning |
|---|---|
| `--mode asap\|casap\|alap\|calap\|custom` | scheduling mode (default `asap`) |
| `--add N --sub N --mul N` | resource limits (constrained modes and `custom`) |
| `--latency L` | step bound for `alap`, `calap`, `custom` |
| `--const C=5,D=3` | hardcode inputs (they no longer come from the switches) |
| `--outputs X,Q` | keep only these variables as outputs |
| `--test A=3,B=2` | test vector (repeatable). The first one also drives `trace.txt` |
| `--random-tests N` | extra random vectors in the testbench (default 4) |
| `--export-ssa [PATH]` | write the SSA form (default `<out>/ssa.txt`) |
| `--compare` | print the five-mode comparison table and stop |
| `--sim` | compile and run the testbench with Icarus; exits non-zero on failure |
| `--out DIR` | output folder (default `generated/<name>_<mode>`) |

Examples:
```powershell
python -m hls examples\pdf_example.txt --test A=3,B=2 --sim
python -m hls examples\parallel.txt --mode casap --add 1 --mul 1 --sim
python -m hls examples\parallel.txt --mode custom --latency 6 --sim
python -m hls examples\reassign.txt --export-ssa
python -m hls examples\invalid_syntax.txt          # shows all four errors
```

---

## Building and programming the FPGA

One command goes from equations to a programmed board:

```powershell
.\scripts\build.ps1 -Equations examples\pdf_example.txt -HlsArgs "--mode casap --add 1 --test A=3,B=2" -Program
```

It runs these steps:
1. `python -m hls … --out build\<name>\design --export-ssa --sim`, and stops if the simulation fails.
2. Vivado in batch mode with `scripts/build.tcl`: synthesis, opt, place, route, then timing/utilization/DRC reports. It stops if setup slack is negative.
3. Writes `build\<name>\top_nexys.bit` and programs the board over JTAG if `-Program` is given.

The full build takes about **4–5 minutes**.

Other uses:
```powershell
.\scripts\build.ps1 -Design generated\pdf_example_asap              # build an existing design folder
.\scripts\build.ps1 -Design build\pdf_example\design -ProgramOnly   # re-flash without rebuilding
.\scripts\build.ps1 ... -Part xc7a50ticsg324-1L                     # Nexys A7-50T
```
Without PowerShell:
```
vivado -mode batch -source scripts/build.tcl -tclargs <design_dir> [out_dir] [part] [program]
```

Reports land in `build\<name>\`: `timing.rpt`, `utilization.rpt`, `drc.rpt`, `vivado.log`. For `pdf_example` (casap, 1 adder), the design uses 171 LUTs, 216 FFs and 1 DSP, with +4.7 ns setup slack at 100 MHz.

---

## Using the board

```
 SW[15:14]  view    00 = functional-unit outputs   01 = program outputs
                    10 = registers                 11 = inputs
 SW[13:8]   index within the view
 SW[7:0]    8-bit value to load into an input

 BTNC       STEP: advance the FSM one state (the "clock" for the demo)
 BTNU       LOAD: write SW[7:0] into the input selected by SW[13:8] (view 11)
 BTNL       RESTART: back to IDLE, inputs kept (rerun with new inputs)
 CPU_RESET  clear everything

 7-seg      [7:6] FSM state   [5:4] index   [3:0] selected value (hex)
 LED15      DONE
 LED13-12   current view
 LED11-0    which functional units are busy in this state
```

The core always runs on the 100 MHz clock. Each button press becomes a one-cycle clock **enable** rather than a real clock edge, which avoids bouncing and keeps Vivado's clock routing clean while still stepping one state per press.

`io_map.txt` lists what every index means for that design, and `trace.txt` lists the expected display value after each press. Inputs are always loaded the same way:

1. Press CPU_RESET.
2. Set SW[15:14] = `11`. For each input, set SW[13:8] to its index and SW[7:0] to its value, then press BTNU.
3. Press BTNC to leave IDLE. In each state, set SW[15:14] = `00` and flip SW[13:8] through the busy units to watch them compute. Press BTNC to store the results.
4. At DONE (LED15 on), set SW[15:14] = `01` to read the outputs.

---

## Testing

```powershell
python -m pytest -q          # 439 tests; the Verilog ones are skipped without iverilog
```

The suite covers:
- parser errors
- SSA renaming
- DAG structure
- all five schedulers: dependency order, resource limits, latency bounds and error cases
- binding: unit counts, register sharing and mux sizes
- generated Verilog: compiled and simulated for every example in every mode, against the Python model

Board-level simulation (bouncing buttons, input loading, views, LEDs, display):
```powershell
python -m hls examples\pdf_example.txt --out build\pdf_asap
iverilog -o build\tb_top.vvp tb\tb_top_nexys.v rtl\*.v build\pdf_asap\hls_*.v
vvp build\tb_top.vvp          # PASS: tb_top_nexys, 21 checks
```

---

## 30-minute demo plan

The plan is built around the ~4.5-minute Vivado build. Each professor-directed run is compiled while the previous one is being shown on the board, so no time is spent waiting.

### Before the slot (not counted)
- Nexys A7 plugged in and powered. `vivado`, `iverilog` and `python` on PATH.
- `python -m pytest -q` is green.
- The `pdf_example` bitstream is already built (`.\scripts\build.ps1 -Equations examples\pdf_example.txt -HlsArgs "--test A=3,B=2"`), so the board can be shown right away.
- Two PowerShell windows open in the repo root: **A** for tool runs and **B** for Vivado builds. `README.md` is open for the switch reference.
- An empty `demo\` folder where the professor's equations will go.

### Timeline

| Time | Segment | What happens |
|---|---|---|
| **0:00–3:00** | Overview | The architecture diagram above and the repository layout. One minute on design choices: 16-bit datapath, 8-bit switch inputs, button as clock enable, `hls_core` port list is the same for every design. |
| **3:00–7:00** | Software tour | In window A: <br>1. `python -m hls examples\invalid_syntax.txt`: all errors with line numbers.<br>2. `python -m hls examples\reassign.txt --export-ssa`, then open `ssa.txt`: renaming.<br>3. `python -m hls examples\parallel.txt --compare --latency 6`: five modes, custom uses 4 units instead of 7.<br>4. `python -m hls examples\parallel.txt --mode casap --add 1 --mul 1 --sim`: the constrained schedule, binding report and simulation **PASS**. |
| **7:00–8:30** | **Run 1: professor's equations (unconstrained)** | The professor writes `demo\run1.txt` (up to 20 lines, `+ - *`) and picks input values. In window B: `.\scripts\build.ps1 -Equations demo\run1.txt -HlsArgs "--test A=…,B=…" -Program`. The Icarus simulation result appears within seconds, then Vivado starts. |
| **8:30–12:00** | Generated output for Run 1 | While Vivado builds: open `build\run1\design\`:<br>• `schedule.txt`: steps, FU binding, register sharing<br>• `hls_datapath.v`: the FU instances, muxes and registers<br>• `hls_controller.v`: the FSM and its control signal table<br>• `testbench.v`: how it checks itself<br>• `trace.txt`: the expected display after each press.<br>Meanwhile the professor writes `demo\run2.txt` and picks a **resource constraint** (e.g. 1 adder, 1 multiplier). |
| **12:00–12:30** | Start Run 2 build | Run 1 finishes and flashes. Window B: `.\scripts\build.ps1 -Equations demo\run2.txt -HlsArgs "--mode casap --add 1 --mul 1 --test …"` (no `-Program`, so the board keeps Run 1). |
| **12:30–17:00** | **Run 1 on the board** | Load the inputs (view 11, BTNU). Step with BTNC; in each state show the busy-unit LEDs and use SW[13:8] in view 00 to show each FU computing. Read the outputs in view 01 and check them against the professor's hand calculation and `trace.txt`. Then press BTNL, load different inputs and rerun without rebuilding. Meanwhile the professor writes `demo\run3.txt` for the **custom scheduler** or **ALAP** with a latency bound, optionally hardcoding an input with `--const`. |
| **17:00–17:30** | Flash Run 2, start Run 3 build | `.\scripts\build.ps1 -Design build\run2\design -ProgramOnly`, then in window B start `.\scripts\build.ps1 -Equations demo\run3.txt -HlsArgs "--mode custom --latency N --const C=… --test …"`. |
| **17:30–22:30** | **Run 2 on the board: resource sharing** | In window A, `python -m hls demo\run2.txt --compare --add 1 --mul 1` shows how the constraint stretched the schedule compared with ASAP. On the board there are fewer FUs, so the **same adder** shows different operations in different states (view 00, index 0), and the mux selects change per state. Check the outputs. |
| **22:30–23:00** | Flash Run 3 | `.\scripts\build.ps1 -Design build\run3\design -ProgramOnly` |
| **23:00–27:00** | **Run 3 on the board: custom/ALAP + hardcoded input** | Show the custom scheduler meeting the latency with the fewest units (`schedule.txt`), and that the hardcoded input is not in the input view. Step through and check the outputs. |
| **27:00–28:30** | Verification | `python -m pytest -q` (439 passed) and the board-level testbench (`tb_top_nexys`, 21 checks). |
| **28:30–30:00** | Questions | Buffer time. |

### Every requirement is shown live

| Requirement | Where in the demo |
|---|---|
| Parse equation file | 3:00 tour, all three runs |
| SSA + `--export-ssa` | 3:00 tour (`reassign.txt`), each build writes `ssa.txt` |
| DAG | `schedule.txt`, `dag.dot` |
| ASAP / ALAP / constrained ASAP / constrained ALAP / custom | `--compare` in the tour; Run 1 ASAP, Run 2 constrained, Run 3 custom/ALAP |
| Mux + register datapath, FSM | `hls_datapath.v`, `hls_controller.v` during the Run 1 build |
| Primitive FUs, exact instance count | `hls_datapath.v` and the busy-unit LEDs |
| Testbench | `--sim` PASS on every run, pytest, `tb_top_nexys` |
| Button-stepped clock | BTNC on every run |
| Selectable FU observability | view 00 + SW[13:8] on every run |
| FPGA deployment + 7-segment output | Runs 1–3 |
| Three professor-directed runs | Runs 1–3 |

### Contingencies
- **Vivado build fails or is slow:** the `--sim` PASS has already shown the design is correct. Fall back to the prebuilt `pdf_example` bitstream and use the professor's *input values* with BTNL, which needs no rebuild.
- **Equation error:** the parser lists every bad line at once. Fix it in place and rerun (the build stops before Vivado, so no time is lost).
- **Constraint can't be met:** the tool explains why (e.g. *"latency 3 is shorter than the critical path (4 steps)"* or *"custom: --latency 6 can't be met with these resource limits (best found: 7 steps)"*). That's worth showing, then relax the bound.
- **Values too large:** inputs are 8-bit (0–255) and results wrap at 2¹⁶. Pick values whose results fit in 16 bits, or use `--const` for a larger constant (any 16-bit value).
