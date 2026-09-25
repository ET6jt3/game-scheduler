param(
  [Parameter(Mandatory=$true)][string]$PreviousRoot,
  [switch]$IncludeLogs
)
$ErrorActionPreference='Stop'
$PreviousRoot=[IO.Path]::GetFullPath($PreviousRoot)
$NewRoot=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
if($PreviousRoot.TrimEnd('\') -ieq $NewRoot.TrimEnd('\')){throw 'PreviousRoot must point to the older portable package, not this package.'}
if(!(Test-Path -LiteralPath (Join-Path $PreviousRoot 'App\server.exe'))){throw "Previous portable package not found: $PreviousRoot"}
if(!(Test-Path -LiteralPath (Join-Path $NewRoot 'App\server.exe'))){throw "New portable package is incomplete: $NewRoot"}

function Assert-NotRunning([string]$Root){
  $cfg=Join-Path $Root 'Config\config.json'
  if(!(Test-Path -LiteralPath $cfg)){return}
  try{
    $c=Get-Content -LiteralPath $cfg -Raw | ConvertFrom-Json
    $addr=$c.addr
    if(!$addr){$addr='127.0.0.1:8080'}
    if($addr -match '^(127\.0\.0\.1|localhost):[0-9]+$'){
      $h=Invoke-RestMethod -Uri ('http://'+$addr+'/healthz') -TimeoutSec 1
      if($h -and $h.root){
        $owner=[IO.Path]::GetFullPath($h.root).TrimEnd('\')
        $oldApp=[IO.Path]::GetFullPath((Join-Path $Root 'App')).TrimEnd('\')
        if($owner -ieq $oldApp){throw "The previous scheduler is still running. Run its Stop.cmd first: $Root"}
      }
    }
  }catch{
    if($_.Exception.Message -like 'The previous scheduler is still running*'){throw}
  }
}
Assert-NotRunning $PreviousRoot
Assert-NotRunning $NewRoot

$stamp=Get-Date -Format 'yyyyMMdd-HHmmss'
$backup=Join-Path $NewRoot ('Backups\pre-migration-'+$stamp)
[void][IO.Directory]::CreateDirectory($backup)

# Preserve any state already present in the new package before replacing it.
foreach($rel in @('Data','Config\config.json','Config\helpers','Helpers','Runtime')){
  $src=Join-Path $NewRoot $rel
  if(Test-Path -LiteralPath $src){
    $safe=$rel.Replace('\','__')
    Copy-Item -LiteralPath $src -Destination (Join-Path $backup $safe) -Recurse -Force
  }
}

# scheduler.db and Data contain games/tasks/plans/chains/helper instances/history.
if(Test-Path -LiteralPath (Join-Path $PreviousRoot 'Data')){
  Copy-Item -Path (Join-Path $PreviousRoot 'Data\*') -Destination (Join-Path $NewRoot 'Data') -Recurse -Force
}
if(Test-Path -LiteralPath (Join-Path $PreviousRoot 'Config\config.json')){
  Copy-Item -LiteralPath (Join-Path $PreviousRoot 'Config\config.json') -Destination (Join-Path $NewRoot 'Config\config.json') -Force
}
if(Test-Path -LiteralPath (Join-Path $PreviousRoot 'Config\helpers')){
  $newHelperDir=Join-Path $NewRoot 'Config\helpers'
  $oldHelperDir=Join-Path $PreviousRoot 'Config\helpers'
  [void][IO.Directory]::CreateDirectory($newHelperDir)

  # New-package helper definitions are code/version contracts, not user state.
  # Preserve them when the same filename exists in the new package. This is
  # especially important for ok-nte, whose worker/lifecycle schema can change
  # between scheduler releases. User-added definitions with unique filenames
  # still migrate normally. Keep a copy of every skipped old definition in the
  # migration backup so deliberate customizations can be reconciled manually.
  $skippedOld=Join-Path $backup 'previous-Config__helpers'
  foreach($source in Get-ChildItem -LiteralPath $oldHelperDir -File){
    $destination=Join-Path $newHelperDir $source.Name
    if(Test-Path -LiteralPath $destination){
      [void][IO.Directory]::CreateDirectory($skippedOld)
      Copy-Item -LiteralPath $source.FullName -Destination (Join-Path $skippedOld $source.Name) -Force
      Write-Host ('Keeping new helper definition; previous copy backed up: '+$source.Name)
    }else{
      Copy-Item -LiteralPath $source.FullName -Destination $destination -Force
    }
  }
}
foreach($rel in @('Helpers','Runtime')){
  $src=Join-Path $PreviousRoot $rel
  if(Test-Path -LiteralPath $src){
    [void][IO.Directory]::CreateDirectory((Join-Path $NewRoot $rel))
    Copy-Item -Path (Join-Path $src '*') -Destination (Join-Path $NewRoot $rel) -Recurse -Force
  }
}
if($IncludeLogs -and (Test-Path -LiteralPath (Join-Path $PreviousRoot 'Logs'))){
  Copy-Item -Path (Join-Path $PreviousRoot 'Logs\*') -Destination (Join-Path $NewRoot 'Logs') -Recurse -Force
}

Write-Host ''
Write-Host 'Migration complete.'
Write-Host ('Previous package: '+$PreviousRoot)
Write-Host ('New package:      '+$NewRoot)
Write-Host ('Backup:           '+$backup)
Write-Host ''
Write-Host 'Your games, tasks, daily chains, helper instances and execution history live in Data\scheduler.db and were transferred.'
Write-Host 'External helper paths are stored as paths and remain external; they were not copied or changed.'
Write-Host 'If Windows auto-start was enabled, run Setup-Startup.cmd in THIS new package so the scheduled task points to the new folder.'
