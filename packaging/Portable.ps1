param([ValidateSet('Start','Stop')][string]$Action='Start', [switch]$NoBrowser)
$ErrorActionPreference='Stop'
$packageRoot=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$configPath=Join-Path $packageRoot 'Config\config.json'
$serverPath=Join-Path $PSScriptRoot 'server.exe'
foreach($name in @('Data','Logs','Backups','Helpers','Runtime')){[void][IO.Directory]::CreateDirectory((Join-Path $packageRoot $name))}
if(!(Test-Path -LiteralPath $configPath)){Copy-Item -LiteralPath (Join-Path $packageRoot 'Config\config.example.json') -Destination $configPath}
$config=Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
$addr=$config.addr
if($env:GS_ADDR){$addr=$env:GS_ADDR}
if(!$addr){$addr='127.0.0.1:8080'}
# Only loopback launch/stop targets are supported by this portable launcher.
if($addr -notmatch '^(127\.0\.0\.1|localhost):[0-9]+$'){throw 'Portable launcher requires addr 127.0.0.1:<port> or localhost:<port>.'}
$url='http://'+$addr
function Get-Health {try{return Invoke-RestMethod -Uri ($url+'/healthz') -TimeoutSec 2}catch{return $null}}
function Assert-Owner($health){if(!$health.root -or [IO.Path]::GetFullPath($health.root).TrimEnd('\') -ine $PSScriptRoot.TrimEnd('\')){throw "The port belongs to another server. Change Config\config.json addr before starting this package."}}
$sha=[Security.Cryptography.SHA256]::Create()
$rootHash=[BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($packageRoot.ToLowerInvariant()))).Replace('-','')
$sha.Dispose()
$mutex=New-Object Threading.Mutex($false,('Local\GameSchedulerPortable-'+$rootHash))
$held=$false
try{
 try{$held=$mutex.WaitOne(0)}catch [Threading.AbandonedMutexException]{$held=$true}
 if(!$held){throw 'Another launcher is starting or stopping this package.'}
 $health=Get-Health
 if($Action -eq 'Stop'){
  if(!$health){Write-Host 'Scheduler is not responding on the configured address; no process was terminated.';exit 0}
  Assert-Owner $health
  $headers=@{};$auth=$config.auth_token;if($env:GS_AUTH_TOKEN){$auth=$env:GS_AUTH_TOKEN};if($auth){$headers.Authorization='Bearer '+$auth.Trim()}
  Invoke-RestMethod -Method Post -Uri ($url+'/api/server/stop') -Headers $headers -ContentType 'application/json' -Body '{}' -TimeoutSec 5 | Out-Null
  for($i=0;$i -lt 60;$i++){Start-Sleep -Milliseconds 250;if(!(Get-Health)){Write-Host 'Scheduler stopped.';exit 0}}
  throw 'Shutdown still in progress. No unrelated process was terminated.'
 }
 if($health){Assert-Owner $health;Write-Host 'Scheduler is already running.'}
 else{
  if(!(Test-Path -LiteralPath $serverPath)){throw "Missing $serverPath"}
  $stamp=Get-Date -Format 'yyyyMMdd-HHmmss-fff'
  $outLog=Join-Path $packageRoot ('Logs\server-'+$stamp+'.stdout.log')
  $errLog=Join-Path $packageRoot ('Logs\server-'+$stamp+'.stderr.log')
  $child=Start-Process -FilePath $serverPath -ArgumentList @('-config',('"'+$configPath+'"')) -WorkingDirectory $PSScriptRoot -RedirectStandardOutput $outLog -RedirectStandardError $errLog -PassThru
  for($i=0;$i -lt 80;$i++){$child.Refresh();if($child.HasExited){throw "Server exited ($($child.ExitCode)). Logs: $outLog and $errLog"};$health=Get-Health;if($health){Assert-Owner $health;break};Start-Sleep -Milliseconds 250}
  if(!$health){throw "Health check timed out. Inspect $outLog and $errLog"}
 }
 Write-Host ('Dashboard: '+$url)
 if(!$NoBrowser){Start-Process $url}
}finally{if($held){$mutex.ReleaseMutex()};$mutex.Dispose()}
