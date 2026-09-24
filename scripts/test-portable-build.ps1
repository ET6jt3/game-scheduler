$ErrorActionPreference='Stop'
$repoRoot=Split-Path $PSScriptRoot -Parent
Set-Location $repoRoot
$oldPath=$env:PATH
$oldGoRoot=$env:GOROOT
$oldGoPath=$env:GOPATH
$oldTemp=$env:TEMP
$oldAppData=$env:APPDATA
$bootstrap=Join-Path $PSScriptRoot 'portable-go.ps1'
try{
 # Test Windows PowerShell 5.1 with neither Go nor Git available from PATH.
 $env:PATH="$env:SystemRoot\System32;$env:SystemRoot;$env:SystemRoot\System32\WindowsPowerShell\v1.0"
 if(Get-Command go -ErrorAction SilentlyContinue){throw 'Test requires no Go on PATH'}
 $env:GOROOT='Z:\nonexistent-system-go'
 $env:GOPATH='Z:\nonexistent-user-cache'
 $downloadDir=Join-Path $repoRoot 'Toolchain\downloads'
 [void][IO.Directory]::CreateDirectory($downloadDir)
 $archive=Join-Path $downloadDir 'go1.26.8.windows-amd64.zip'
 if(Test-Path $archive){throw 'Test requires a clean toolchain directory'}
 Set-Content -LiteralPath $archive -Value 'deliberately invalid test archive'
 $rejected=$false
 try{& $bootstrap -GoArgs @('version')}catch{if($_ -match 'SHA256 verification'){$rejected=$true}else{throw}}
 if(!$rejected){throw 'Corrupt archive was accepted'}
 Remove-Item -LiteralPath $archive
 Write-Host 'PASS: corrupt archive rejected before extraction'
 & .\Build.cmd
 if($LASTEXITCODE -ne 0){throw 'First build failed'}
 $first=(Get-Content dist\last-build.txt -Raw).Trim()
 if(!(Test-Path (Join-Path $first 'App\server.exe'))){throw 'First build missing server'}
 $elevatedLauncher=Join-Path $first 'Run-Elevated.cmd'
 if(!(Test-Path -LiteralPath $elevatedLauncher)){throw 'First build missing Run-Elevated.cmd'}
 $elevatedText=Get-Content -LiteralPath $elevatedLauncher -Raw
 if($elevatedText -notmatch 'Portable\.ps1.*-Action Stop' -or $elevatedText -notmatch '-Verb RunAs'){
  throw 'Run-Elevated.cmd does not stop safely then request elevation'
 }
 $before=(Get-FileHash (Join-Path $first 'App\server.exe')).Hash
 Set-Content -LiteralPath (Join-Path $first 'Data\preserve.txt') -Value 'keep existing user data'
 & .\Build.cmd
 if($LASTEXITCODE -ne 0){throw 'Second build failed'}
 $second=(Get-Content dist\last-build.txt -Raw).Trim()
 if($first -eq $second -or !(Test-Path (Join-Path $second 'App\server.exe'))){throw 'Repeated build did not choose a new output'}
 if((Get-FileHash (Join-Path $first 'App\server.exe')).Hash -ne $before -or !(Test-Path (Join-Path $first 'Data\preserve.txt'))){throw 'Existing package was modified'}
 Write-Host 'PASS: two builds without system Go; existing package preserved'
 # A newly downloaded/extracted package can import all persistent state from
 # the previous portable folder without copying old binaries.
 Set-Content -LiteralPath (Join-Path $first 'Config\config.json') -Value '{"addr":"127.0.0.1:18080","data_dir":"${ROOT}/../Data","db_path":"${DATA}/scheduler.db"}' -Encoding UTF8
 Set-Content -LiteralPath (Join-Path $first 'Helpers\managed-marker.txt') -Value 'managed helper state'
 Set-Content -LiteralPath (Join-Path $first 'Runtime\runtime-marker.txt') -Value 'runtime state'
 # Simulate a stale package-owned helper definition from an older release and
 # a user-added helper definition that must migrate.
 $oldHelperDir=Join-Path $first 'Config\helpers'
 [void][IO.Directory]::CreateDirectory($oldHelperDir)
 Set-Content -LiteralPath (Join-Path $oldHelperDir 'ok-nte.json') -Value '{"schema_version":1,"stale":true}' -Encoding UTF8
 Set-Content -LiteralPath (Join-Path $oldHelperDir 'user-custom.json') -Value '{"custom":true}' -Encoding UTF8
 & (Join-Path $second 'App\Migrate-From-Previous.ps1') -PreviousRoot $first
 if(!(Test-Path (Join-Path $second 'Data\preserve.txt'))){throw 'Migration did not copy Data'}
 if((Get-Content (Join-Path $second 'Config\config.json') -Raw) -notmatch '18080'){throw 'Migration did not copy config.json'}
 if(!(Test-Path (Join-Path $second 'Helpers\managed-marker.txt')) -or !(Test-Path (Join-Path $second 'Runtime\runtime-marker.txt'))){throw 'Migration did not copy managed helper/runtime state'}
 $newNte=Get-Content -LiteralPath (Join-Path $second 'Config\helpers\ok-nte.json') -Raw
 if($newNte -notmatch '"lifecycle_mode"' -or $newNte -notmatch '"native"'){throw 'Migration overwrote current ok-nte lifecycle definition'}
 if(!(Test-Path -LiteralPath (Join-Path $second 'Config\helpers\user-custom.json'))){throw 'Migration did not copy user-added helper definition'}
 $migrationBackup=Get-ChildItem (Join-Path $second 'Backups') -Directory | Where-Object Name -like 'pre-migration-*' | Select-Object -First 1
 if(!$migrationBackup){throw 'Migration did not create safety backup'}
 if(!(Test-Path -LiteralPath (Join-Path $migrationBackup.FullName 'previous-Config__helpers\ok-nte.json'))){throw 'Migration did not back up skipped previous ok-nte definition'}
 Write-Host 'PASS: previous portable state migrates; current built-in definitions win; user helper definitions are preserved'
 $goEnv=(& $bootstrap -GoArgs @('env','-json','GOROOT','GOPATH','GOMODCACHE','GOCACHE','GOTMPDIR','GOENV','GOTOOLCHAIN','GOTELEMETRY','GOTELEMETRYDIR')) -join "`n" | ConvertFrom-Json
 foreach($key in @('GOROOT','GOPATH','GOMODCACHE','GOCACHE','GOTMPDIR','GOTELEMETRYDIR')){
  if(!$goEnv.$key.StartsWith((Join-Path $repoRoot 'Toolchain'),[StringComparison]::OrdinalIgnoreCase)){throw "$key escaped repository: $($goEnv.$key)"}
 }
 # GOENV=off is reported as an empty filename by `go env GOENV`.
 if($goEnv.GOENV -ne '' -or $goEnv.GOTOOLCHAIN -ne 'local' -or $goEnv.GOTELEMETRY -ne 'off'){throw ('Go isolation settings incorrect: '+($goEnv | ConvertTo-Json -Compress))}
 if($env:GOROOT -ne 'Z:\nonexistent-system-go' -or $env:GOPATH -ne 'Z:\nonexistent-user-cache' -or $env:TEMP -ne $oldTemp -or $env:APPDATA -ne $oldAppData){throw 'Caller environment was not restored'}
 Write-Host 'PASS: repository-local Go environment and restored caller settings'
 # Relocate the compiler to a repository path with spaces and invoke it again.
 $relocated=Join-Path $repoRoot 'dist\relocated repo space'
 [void][IO.Directory]::CreateDirectory((Join-Path $relocated 'scripts'))
 Copy-Item -LiteralPath $bootstrap -Destination (Join-Path $relocated 'scripts')
 Move-Item -LiteralPath (Join-Path $repoRoot 'Toolchain') -Destination $relocated
 try{
  $newRoot=& (Join-Path $relocated 'scripts\portable-go.ps1') -GoArgs @('env','GOROOT')
  if(!$newRoot.StartsWith($relocated,[StringComparison]::OrdinalIgnoreCase)){throw 'Relocated Go retained old root'}
 }finally{Move-Item -LiteralPath (Join-Path $relocated 'Toolchain') -Destination $repoRoot}
 Write-Host 'PASS: portable compiler relocation to path with spaces'
 & $bootstrap -GoArgs @('build','-o',(Join-Path $repoRoot 'dist\fake-helper.exe'),'./scripts/testdata/fake-helper')
}finally{
 $env:PATH=$oldPath;$env:GOROOT=$oldGoRoot;$env:GOPATH=$oldGoPath
}
