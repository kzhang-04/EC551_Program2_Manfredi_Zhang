<#
.SYNOPSIS
  Equations to programmed Nexys A7 in one command.

.EXAMPLE
  # synthesize equations, simulate, build the bitstream and flash the board
  .\scripts\build.ps1 -Equations examples\pdf_example.txt -HlsArgs "--mode casap --add 1 --test A=3,B=2" -Program

.EXAMPLE
  # build a design folder that python -m hls already wrote
  .\scripts\build.ps1 -Design generated\pdf_example_asap

.EXAMPLE
  # flash an existing bitstream without rebuilding
  .\scripts\build.ps1 -Design generated\pdf_example_asap -ProgramOnly
#>
param(
    [string]$Equations,                    # run the HLS tool on this file first
    [string]$HlsArgs = "",                 # extra arguments for python -m hls
    [string]$Design,                       # existing design folder (skips the HLS step)
    [string]$Part = "xc7a100tcsg324-1",
    [switch]$Program,                      # flash the board after building
    [switch]$ProgramOnly,                  # flash build/<name>/top_nexys.bit and stop
    [switch]$NoSim                         # skip the Icarus testbench run
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot

if (-not $Equations -and -not $Design) { throw "give -Equations <file> or -Design <folder>" }

# Find Vivado: PATH first, then the usual install folders (newest first).
$vivado = (Get-Command vivado.bat -ErrorAction SilentlyContinue).Source
if (-not $vivado) {
    $vivado = Get-ChildItem "C:\AMDDesignTools\*\Vivado\bin\vivado.bat", "C:\Xilinx\Vivado\*\bin\vivado.bat" `
                  -ErrorAction SilentlyContinue | Sort-Object FullName -Descending | Select-Object -First 1 -ExpandProperty FullName
}
if (-not $vivado) { throw "Vivado not found; add its bin folder to PATH" }

# Step 1: equations -> Verilog (and a self-checking simulation unless -NoSim).
if ($Equations) {
    $name = [IO.Path]::GetFileNameWithoutExtension($Equations)
    $Design = Join-Path $Root "build\$name\design"
    $hls = @("-m", "hls", $Equations, "--out", $Design, "--export-ssa")
    if (-not $NoSim) { $hls += "--sim" }
    if ($HlsArgs) { $hls += $HlsArgs -split '\s+' | Where-Object { $_ } }
    Write-Host "== python $($hls -join ' ')" -ForegroundColor Cyan
    Push-Location $Root
    try { & python @hls; if ($LASTEXITCODE -ne 0) { throw "HLS tool or simulation failed" } }
    finally { Pop-Location }
}

$Design = (Resolve-Path $Design).Path
$name = Split-Path -Leaf $Design
if ($name -eq "design") { $name = Split-Path -Leaf (Split-Path -Parent $Design) }
$OutDir = Join-Path $Root "build\$name"

if ($ProgramOnly) {
    $bit = Join-Path $OutDir "top_nexys.bit"
    if (-not (Test-Path $bit)) { throw "no bitstream at $bit; build first" }
    $tcl = Join-Path $OutDir "program.tcl"
    @"
open_hw_manager
connect_hw_server -allow_non_jtag
open_hw_target
set dev [lindex [get_hw_devices xc7a*] 0]
current_hw_device `$dev
set_property PROGRAM.FILE {$($bit -replace '\\','/')} `$dev
program_hw_devices `$dev
"@ | Set-Content -Encoding ascii $tcl
    & $vivado -mode batch -nojournal -nolog -source $tcl
    exit $LASTEXITCODE
}

# Step 2: Verilog -> bitstream (-> board).
$tclArgs = @($Design, $OutDir, $Part)
if ($Program) { $tclArgs += "program" }
Write-Host "== Vivado: $Design -> $OutDir" -ForegroundColor Cyan
New-Item -ItemType Directory -Force $OutDir | Out-Null
Push-Location $OutDir   # keeps Vivado's .Xil scratch folder out of the repo root
try {
    & $vivado -mode batch -nojournal -log (Join-Path $OutDir "vivado.log") `
        -source (Join-Path $Root "scripts\build.tcl") -tclargs @tclArgs
    if ($LASTEXITCODE -ne 0) { throw "Vivado failed; see $OutDir\vivado.log" }
} finally { Pop-Location }

Write-Host "== done: $OutDir\top_nexys.bit" -ForegroundColor Green
if (Test-Path (Join-Path $Design "io_map.txt")) {
    Write-Host "   switch map: $Design\io_map.txt"
    Write-Host "   expected display per BTNC press: $Design\trace.txt"
}
