# One A2 speed-test run, from the load-generator machine (the desktop), after the laptop has restarted
# Ollama and sent the three warm-up tickets (and, for a cascade, confirmed both models in `ollama ps`).
#
#   .\jmeter\run_a2_speed.ps1 -Config llama -Run 1 -SutHost 192.168.50.1 -DryRun
#   .\jmeter\run_a2_speed.ps1 -Config cascade-A -Run 1 -SutHost 192.168.50.1
#
# Reads the rate, run length, timeout and pause from a2/results/speed_setup.json (written once, after S is
# measured) so every configuration uses the same values. Checks /health shows the expected configuration,
# records /stats before and after, and writes to a2/results/speed/<Config>/run<Run>/:
#   results.jtl   one row per request (CSV, with the ticket ID the service returned)
#   jmeter.log    JMeter's own log
#   run.json      settings, /health, /stats before and after, start and end times
# Refuses to overwrite a recorded run.
param(
    [Parameter(Mandatory)][ValidateSet("llama", "cascade-A", "cascade-B")][string]$Config,
    [Parameter(Mandatory)][ValidateRange(1, 3)][int]$Run,
    [Parameter(Mandatory)][string]$SutHost,
    [int]$Port = 5000,
    [switch]$DryRun
)
$ErrorActionPreference = "Stop"
$repo = Split-Path $PSScriptRoot -Parent

# --- Settings, the same for every configuration ---
$setupPath = Join-Path $repo "a2\results\speed_setup.json"
if (-not (Test-Path $setupPath)) { throw "$setupPath not found: measure S first" }
$setup = Get-Content -Raw $setupPath | ConvertFrom-Json
$S = [double]$setup.S_s
$expect = [ordered]@{
    rate_per_hour = [int][math]::Round(3600 / (2 * $S))
    duration_min  = [int][math]::Max([math]::Ceiling($S), 5)
    timeout_s     = [int][math]::Max([math]::Ceiling(10 * $S), 300)
}
$expect.pause_min = [int][math]::Ceiling(($expect.timeout_s + 60) / 60)
foreach ($k in $expect.Keys) {
    if ([int]$setup.$k -ne $expect[$k]) { throw "speed_setup.json: $k is $($setup.$k), but S = $S gives $($expect[$k])" }
}
$seed = 3113 + $Run   # run N of every configuration gets the same arrival pattern
$runId = "speed-$Config-r$Run"
$narratives = Join-Path $repo "a2\speed_narratives.txt"
$lines = @(Get-Content $narratives -Encoding UTF8 | Where-Object { $_.Trim() })
if ($lines.Count -ne 120) { throw "$narratives has $($lines.Count) tickets, expected 120" }

$outDir = Join-Path $repo "a2\results\speed\$Config\run$Run"
$jtl = Join-Path $outDir "results.jtl"
if (Test-Path $jtl) { throw "$jtl already exists; refusing to overwrite a recorded run" }

# --- The service must be running the configuration we are about to record ---
$base = "http://${SutHost}:$Port"
$health = Invoke-RestMethod "$base/health" -TimeoutSec 10
if ($Config -eq "llama") { $ok = $health.configuration -eq "single" -and $health.model -eq "llama3.1:8b" }
else { $ok = $health.configuration -eq $Config }
if (-not $ok) { throw "/health shows configuration '$($health.configuration)', model '$($health.model)'; expected $Config" }

$jmeterArgs = @("-n", "-t", (Join-Path $PSScriptRoot "a2_speed_test.jmx"),
    "-Jhost=$SutHost", "-Jport=$Port", "-Jrun_id=$runId", "-Jnarratives=$narratives",
    "-Jrate=$($setup.rate_per_hour)", "-Jduration=$($setup.duration_min)", "-Jpause=$($setup.pause_min)",
    "-Jtimeout_ms=$($setup.timeout_s * 1000)", "-Jseed=$seed",
    "-Jjmeter.save.saveservice.output_format=csv", "-Jsample_variables=ticket_id",
    "-l", $jtl, "-j", (Join-Path $outDir "jmeter.log"))

Write-Host "Run $runId against $base ($($health.configuration), $($health.model))"
Write-Host "  S = $S s; $($setup.rate_per_hour) tickets/hour for $($setup.duration_min) min; timeout $($setup.timeout_s) s; pause $($setup.pause_min) min; seed $seed"
Write-Host "  JMeter stays up about $($setup.duration_min + $setup.pause_min) min in total"
if ($DryRun) { Write-Host "Dry run, nothing sent:"; Write-Host "  jmeter $($jmeterArgs -join ' ')"; exit 0 }

New-Item -ItemType Directory -Force $outDir | Out-Null
$utf8 = New-Object System.Text.UTF8Encoding($false)
$record = [ordered]@{
    run_id = $runId; configuration = $Config; run = $Run; sut = $base
    S_s = $S; rate_per_hour = $setup.rate_per_hour; duration_min = $setup.duration_min
    timeout_s = $setup.timeout_s; pause_min = $setup.pause_min; seed = $seed
    narratives = "a2/speed_narratives.txt"; health = $health
    stats_before = (Invoke-RestMethod "$base/stats" -TimeoutSec 10).total
    started_utc = (Get-Date).ToUniversalTime().ToString("o")
}
$runJson = Join-Path $outDir "run.json"
[IO.File]::WriteAllText($runJson, ($record | ConvertTo-Json -Depth 5), $utf8)
Write-Host "  /stats before: $($record.stats_before)"

& jmeter @jmeterArgs
$record.jmeter_exit = $LASTEXITCODE
$record.ended_utc = (Get-Date).ToUniversalTime().ToString("o")
$record.stats_after = (Invoke-RestMethod "$base/stats" -TimeoutSec 10).total
$record.stats_delta = $record.stats_after - $record.stats_before
[IO.File]::WriteAllText($runJson, ($record | ConvertTo-Json -Depth 5), $utf8)

$sent = @(Import-Csv $jtl).Count
Write-Host "Done: $sent requests in results.jtl; /stats after: $($record.stats_after) (+$($record.stats_delta))"
Write-Host "Next, on the laptop: python scripts/a2_save_log.py --run-id $runId-warmup --run-id $runId --out a2/results/speed/$Config/run$Run/requests.log"
