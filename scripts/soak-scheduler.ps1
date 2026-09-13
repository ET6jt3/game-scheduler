# soak-scheduler.ps1 - overnight scheduling-chain soak for game-scheduler.
#
# Boots an ISOLATED temp server instance (own port, own data dir, random
# token), creates a game/route/task against a HARMLESS fake tool (copied
# cmd.exe /c echo), attaches a cron plan, and lets the scheduler fire for
# -Minutes. Samples /api/dashboard totals + server process resources on a
# fixed interval; at the end asserts: zero failed, zero stuck-running, and
# executions_total within tolerance of the cron-math expectation.
#
# Lessons from the 2026-09-12/13 night baked in:
#   - fire counts come from /api/dashboard totals (executions_total), NEVER
#     from a list window (list views cap at 500 rows);
#   - there is no `ctl dashboard` resource: the harness calls the REST API
#     directly with Invoke-RestMethod.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts\soak-scheduler.ps1 -Minutes 60
#
# Exit code 0 = soak PASS. Logs and the CSV sample trail land in the temp
# work dir, whose path is printed at the end.

param(
    [int]$Minutes = 60,
    # cron step in minutes: 1 = "* * * * *" (every minute)
    [int]$StepMinutes = 1,
    # Random per run: a crashed earlier run's server still holding a fixed
    # port makes the NEXT run's healthz pass against the WRONG server (its
    # /healthz is exempt) while every authenticated call 401s. Been there.
    [int]$Port = 18000 + (Get-Random -Minimum 0 -Maximum 2000),
    [string]$ServerExe = ".nightly\bin\soak-server.exe",
    [string]$CtlExe = ".nightly\bin\soak-ctl.exe"
)

$ErrorActionPreference = "Stop"
Set-Location -LiteralPath (Join-Path $PSScriptRoot "..")

if (-not (Test-Path $ServerExe)) {
    Write-Host "== building server ($ServerExe) =="
    go build -p 2 -o $ServerExe ./cmd/server
    if ($LASTEXITCODE -ne 0) { throw "server build failed" }
}
if (-not (Test-Path $CtlExe)) {
    Write-Host "== building ctl ($CtlExe) =="
    go build -p 2 -o $CtlExe ./cmd/ctl
    if ($LASTEXITCODE -ne 0) { throw "ctl build failed" }
}

$work = Join-Path ([System.IO.Path]::GetTempPath()) ("gs-soak-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
New-Item -ItemType Directory -Path $work | Out-Null
$dataDir = Join-Path $work "data"
New-Item -ItemType Directory -Path $dataDir | Out-Null
$token = "soak-" + [guid]::NewGuid().ToString("N")
$fakeExe = Join-Path $work "BetterGI.exe"
Copy-Item "$env:SystemRoot\System32\cmd.exe" $fakeExe

$config = @{
    addr             = "127.0.0.1:$Port"
    data_dir         = $dataDir
    db_path          = (Join-Path $dataDir "scheduler.db")
    max_concurrent   = 1
    auth_token       = $token
    monitor_enabled  = $true
    overload_policy  = "alert"
} | ConvertTo-Json -Compress
$configPath = Join-Path $work "config.json"
Set-Content -Path $configPath -Value $config -Encoding ASCII

$serverLog = Join-Path $work "server.log"
$server = Start-Process -FilePath $ServerExe -ArgumentList @("-config", $configPath) `
    -RedirectStandardOutput $serverLog -RedirectStandardError (Join-Path $work "server.err.log") `
    -PassThru -WindowStyle Hidden

# All HTTP goes through curl.exe (proven against this server; PS 5.1's
# Invoke-RestMethod produced hangs, 401 cross-talk and ProtocolViolation
# surprises in the first smoke runs). JSON bodies pass via temp files —
# embedding them in a PS command line is the quote-mangling trap.
$script:ApiCalls = 0
function Invoke-Api {
    param([string]$Method, [string]$Path, [string]$JsonBody = $null)
    $script:ApiCalls++
    # curl sends -X VERBATIM, and Go 1.22+ ServeMux matches methods
    # case-sensitively ("Post" => 405). Normalize.
    $Method = $Method.ToUpper()
    $argv = @("curl.exe", "-s", "-m", "15", "-X", $Method,
        "-H", "Authorization: Bearer $token",
        "-H", "Content-Type: application/json")
    if ($null -ne $JsonBody) {
        $argv += "--data"
        $argv += "@-"
        $argv += "http://127.0.0.1:$Port$Path"
        $raw = $JsonBody | & $argv[0] @($argv[1..($argv.Count - 1)])
    } else {
        $argv += "http://127.0.0.1:$Port$Path"
        $raw = & $argv[0] @($argv[1..($argv.Count - 1)])
    }
    if ($LASTEXITCODE -ne 0) { throw "curl $Method $Path failed (exit $LASTEXITCODE)" }
    # PUT responses are 204 with an empty body — "" parses as nothing
    $joined = ($raw -join "`n")
    if ([string]::IsNullOrWhiteSpace($joined)) { return $null }
    try {
        return ($joined | ConvertFrom-Json)
    } catch {
        throw "convert failed for $Method $Path - body: [$joined]"
    }
}

# health: the EXEMPT endpoint is root-level /healthz (api.go keeps / and
# /healthz open); /api/healthz does not exist — a 404 there reads as
# "unhealthy" and burned the first smoke run's whole retry budget.
$ready = $false
for ($i = 0; $i -lt 20; $i++) {
    $code = & curl.exe -s -m 5 -o "NUL" -w "%{http_code}" "http://127.0.0.1:$Port/healthz"
    if ("$code" -eq "200") { $ready = $true; break }
    Start-Sleep -Seconds 1
}
if (-not $ready) { throw "server did not become healthy in 20s (see $serverLog)" }
Write-Host "server healthy on 127.0.0.1:$Port (work: $work)"

# fixture: game -> route -> task (harmless raw echo) -> every-minute plan
$gameBody = @{ id = "soak-genshin"; name = "SOAK Genshin"; adapter = "genshin"; tool_path = $fakeExe; enabled = $true } | ConvertTo-Json -Compress
$null = Invoke-Api -Method Post -Path "/api/games" -JsonBody $gameBody
$routeBody = @{ game_id = "soak-genshin"; adapter = "genshin"; route_type = "collect"; name = "soak-route";
    file_path = (Join-Path $work "route.json"); tags = @("soak") } | ConvertTo-Json -Compress
Set-Content -Path (Join-Path $work "route.json") -Value '{"waypoints":[]}' -Encoding ASCII
$route = Invoke-Api -Method Post -Path "/api/routes" -JsonBody $routeBody
$task = Invoke-Api -Method Post -Path "/api/routes/$($route.id)/create-task"
$taskBody = @{ game_id = "soak-genshin"; name = $task.name; type = "raw"; route_id = $task.route_id;
    params = (@{ exe = $fakeExe; raw_args = @("/c", "echo", "SOAK_RUN_OK") } | ConvertTo-Json -Compress);
    enabled = $true } | ConvertTo-Json -Compress -Depth 4
$null = Invoke-Api -Method Put -Path "/api/tasks/$($task.id)" -JsonBody $taskBody

$cron = if ($StepMinutes -le 1) { "* * * * *" } else { "*/$StepMinutes * * * *" }
$planBody = @{ name = "soak-plan"; task_id = $task.id; cron_expr = $cron; enabled = $true } | ConvertTo-Json -Compress
$null = Invoke-Api -Method Post -Path "/api/plans" -JsonBody $planBody
Write-Host "plan armed: cron '$cron' - soaking for $Minutes minute(s)"

# PS 5.1 threw ProtocolViolationException on this GET inside the sampling
# loop (01:3x smoke); curl.exe is proven against this server — use it for
# the hot path.
function Invoke-Dash {
    return (Invoke-Api -Method Get -Path "/api/dashboard")
}

$samples = Join-Path $work "samples.csv"
"utc_time,executions_total,running,failed_24h,server_ws_mb,server_handles" | Set-Content -Path $samples -Encoding ASCII

$deadline = (Get-Date).AddMinutes($Minutes)
$nextSample = (Get-Date).AddMinutes(1)
$peakWs = 0
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 5
    if ((Get-Date) -lt $nextSample) { continue }
    $nextSample = $nextSample.AddMinutes(1)
    $dash = Invoke-Dash
    $proc = Get-Process -Id $server.Id -ErrorAction SilentlyContinue
    $wsMb = if ($proc) { [math]::Round($proc.WorkingSet64 / 1MB, 1) } else { -1 }
    $handles = if ($proc) { $proc.HandleCount } else { -1 }
    if ($wsMb -gt $peakWs) { $peakWs = $wsMb }
    $line = "{0:u},{1},{2},{3},{4},{5}" -f (Get-Date).ToUniversalTime(), $dash.totals.executions_total, `
        $dash.totals.running, $dash.totals.failed_24h, $wsMb, $handles
    Add-Content -Path $samples -Value $line
}

$dash = Invoke-Dash
$total = $dash.totals.executions_total
$running = $dash.totals.running
$failed = $dash.totals.failed_24h

# stop the server AFTER the final reads (Stop-Process: harness teardown)
Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue

$elapsedMin = $Minutes
$expected = [math]::Floor($elapsedMin / [math]::Max(1, $StepMinutes))
$tolerance = 2
$verdict = "PASS"
if ($failed -ne 0) { $verdict = "FAIL (failed_24h=$failed)" }
elseif ($running -ne 0) { $verdict = "FAIL (running stuck=$running)" }
elseif ([math]::Abs($total - $expected) -gt $tolerance) { $verdict = "FAIL (fires $total vs expected ~$expected +- $tolerance)" }

Write-Host "SOAK RESULT: $verdict — fires=$total (expected ~$expected), running=$running, failed_24h=$failed, peak_ws=${peakWs}MB"
Write-Host "samples: $samples"
if ($verdict -ne "PASS") { exit 1 }
exit 0
