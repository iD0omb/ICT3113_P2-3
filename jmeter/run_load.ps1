# One load run (playbook 3.4). Example: .\jmeter\run_load.ps1 -Model gemma3:4b -Rate 50 -Run 1 -SutHost 192.168.1.23
param(
    [Parameter(Mandatory)][string]$Model,
    [Parameter(Mandatory)][ValidateSet(50, 100)][int]$Rate,
    [Parameter(Mandatory)][ValidateRange(1, 3)][int]$Run,
    [Parameter(Mandatory)][string]$SutHost,
    [int]$Duration = 30
)
$ErrorActionPreference = "Stop"

$safe = $Model -replace '[:.]', '_'
$repo = Split-Path $PSScriptRoot -Parent
$outDir = Join-Path $repo "results\$safe\load\$Rate\run$Run"
$jtl = Join-Path $outDir "results.jtl"
if (Test-Path $jtl) { throw "$jtl already exists; refusing to overwrite a recorded run" }
New-Item -ItemType Directory -Force $outDir | Out-Null

jmeter -n -t (Join-Path $PSScriptRoot "load_test.jmx") `
    "-Jhost=$SutHost" "-Jticket_rate=$Rate" "-Jsearch_rate=214" "-Jstats_rate=60" "-Jduration=$Duration" `
    "-Jrun_id=$safe-$Rate-r$Run" "-Jjmeter.save.saveservice.output_format=csv" `
    -l $jtl -j (Join-Path $outDir "jmeter.log")
