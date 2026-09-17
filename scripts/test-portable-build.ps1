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
 $before=(Get-FileHash (Join-Path $first 'App\server.exe')).Hash
 Set-Content -LiteralPath (Join-Path $first 'Data\preserve.txt') -Value 'keep existing user data'
 & .\Build.cmd
 if($LASTEXITCODE -ne 0){throw 'Second build failed'}
 $second=(Get-Content dist\last-build.txt -Raw).Trim()
 if($first -eq $second -or !(Test-Path (Join-Path $second 'App\server.exe'))){throw 'Repeated build did not choose a new output'}
 if((Get-FileHash (Join-Path $first 'App\server.exe')).Hash -ne $before -or !(Test-Path (Join-Path $first 'Data\preserve.txt'))){throw 'Existing package was modified'}
 Write-Host 'PASS: two builds without system Go; existing package preserved'
 $goEnv=(& $bootstrap -GoArgs @('env','-json','GOROOT','GOPATH','GOMODCACHE','GOCACHE','GOTMPDIR','GOENV','GOTOOLCHAIN','GOTELEMETRY','GOTELEMETRYDIR')) -join "`n" | ConvertFrom-Json
 foreach($key in @('GOROOT','GOPATH','GOMODCACHE','GOCACHE','GOTMPDIR','GOTELEMETRYDIR')){
  if(!$goEnv.$key.StartsWith((Join-Path $repoRoot 'Toolchain'),[StringComparison]::OrdinalIgnoreCase)){throw "$key escaped repository: $($goEnv.$key)"}
 }
 if($goEnv.GOENV -ne 'off' -or $goEnv.GOTOOLCHAIN -ne 'local' -or $goEnv.GOTELEMETRY -ne 'off'){throw 'Go isolation settings incorrect'}
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
