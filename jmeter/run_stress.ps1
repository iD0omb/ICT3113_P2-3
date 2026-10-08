# Stress test (playbook 5). Example: .\jmeter\run_stress.ps1 -Model llama3.1:8b -EndRate 1500 -SutHost 192.168.50.104
param(
    [Parameter(Mandatory)][string]$Model,
    [Parameter(Mandatory)][int]$EndRate,
    [Parameter(Mandatory)][string]$SutHost,
    [int]$StartRate = 50,
    [int]$Duration = 60
)
$ErrorActionPreference = "Stop"

$safe = $Model -replace '[:.]', '_'
$repo = Split-Path $PSScriptRoot -Parent
$outDir = Join-Path $repo "results\stress\$safe"
$jtl = Join-Path $outDir "results.jtl"
if (Test-Path $jtl) { throw "$jtl already exists; refusing to overwrite a recorded run" }
New-Item -ItemType Directory -Force $outDir | Out-Null

jmeter -n -t (Join-Path $PSScriptRoot "stress_test.jmx") `
    "-Jhost=$SutHost" "-Jstart_rate=$StartRate" "-Jend_rate=$EndRate" "-Jduration=$Duration" `
    "-Jrun_id=$safe-stress" "-Jjmeter.save.saveservice.output_format=csv" `
    -l $jtl -j (Join-Path $outDir "jmeter.log")
