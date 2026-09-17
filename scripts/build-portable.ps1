param([string]$OutputDir='', [string]$ControllerPath='')
$ErrorActionPreference='Stop'
$repoRoot=Split-Path $PSScriptRoot -Parent
if(!$OutputDir){
 $OutputDir=Join-Path $repoRoot 'dist'
 if((Test-Path (Join-Path $OutputDir 'GameScheduler-Portable')) -or (Test-Path (Join-Path $OutputDir 'GameScheduler-Portable-Windows-x64.zip'))){
  $OutputDir=Join-Path $OutputDir ('build-'+(Get-Date -Format 'yyyyMMdd-HHmmss')+'-'+[Guid]::NewGuid().ToString('N').Substring(0,8))
 }
}
$OutputDir=[IO.Path]::GetFullPath($OutputDir)
$stage=Join-Path $OutputDir 'GameScheduler-Portable'
if(Test-Path $stage){throw "Staging directory already exists: $stage. Use a fresh OutputDir."}
if(Test-Path (Join-Path $OutputDir 'GameScheduler-Portable-Windows-x64.zip')){throw 'Output ZIP already exists. Use a fresh OutputDir.'}
foreach($name in @('App','Config\helpers','Helpers','Runtime','Data','Logs','Backups')){[void][IO.Directory]::CreateDirectory((Join-Path $stage $name))}
Push-Location $repoRoot
$oldGOOS=$env:GOOS;$oldGOARCH=$env:GOARCH;$oldCGO=$env:CGO_ENABLED
try{
 $env:GOOS='windows';$env:GOARCH='amd64';$env:CGO_ENABLED='0'
 $revision='source'
 if(Get-Command git -ErrorAction SilentlyContinue){$gitRevision=git rev-parse --short=12 HEAD 2>$null;if($LASTEXITCODE -eq 0){$revision=$gitRevision}}
 foreach($cmd in @('server','ctl')){
  Write-Host "Building $cmd with repository-local Go ..."
  & (Join-Path $PSScriptRoot 'portable-go.ps1') -GoArgs @('build','-trimpath','-ldflags',"-s -w -X github.com/xiabee/game-scheduler/internal/version.Version=portable-$revision",'-o',(Join-Path $stage "App\$cmd.exe"),"./cmd/$cmd")
 }
 foreach($cmd in @('Start.cmd','Stop.cmd')){Copy-Item (Join-Path $repoRoot "packaging\$cmd") $stage}
 Copy-Item (Join-Path $repoRoot 'packaging\Portable.ps1') (Join-Path $stage 'App')
 Copy-Item (Join-Path $repoRoot 'packaging\config.example.json') (Join-Path $stage 'Config')
 Copy-Item (Join-Path $repoRoot 'Config\helpers\ok-nte.json') (Join-Path $stage 'Config\helpers')
 foreach($file in @('LICENSE','PORTABLE-LAYOUT.md','MIGRATION-NOTES.md','IMPLEMENTATION-REPORT.md','TEST-REPORT.md')){if(Test-Path $file){Copy-Item $file $stage}}
 Copy-Item (Join-Path $repoRoot 'packaging\README.md') (Join-Path $stage 'README.md')
 Copy-Item (Join-Path $repoRoot 'examples\helper-instances.json') (Join-Path $stage 'Config\helper-instances.example.json')
 Set-Content -LiteralPath (Join-Path $stage 'Helpers\README.txt') -Value 'Optional managed helpers. Install only by explicit choice; external helpers may live anywhere.' -Encoding UTF8
 Set-Content -LiteralPath (Join-Path $stage 'Runtime\README.txt') -Value 'Optional portable runtimes. Configure an explicit Python interpreter for Python helpers.' -Encoding UTF8
 if($ControllerPath){Copy-Item -LiteralPath $ControllerPath -Destination (Join-Path $stage 'App\controller.exe')}
 $zip=Join-Path $OutputDir 'GameScheduler-Portable-Windows-x64.zip'
 Compress-Archive -Path $stage -DestinationPath $zip
 $hash=(Get-FileHash -Algorithm SHA256 -LiteralPath $zip).Hash.ToLowerInvariant()
 Set-Content -LiteralPath ($zip+'.sha256') -Value ($hash+'  '+[IO.Path]::GetFileName($zip)) -Encoding ASCII
 Write-Host $zip
 Write-Host ('Start this build: '+(Join-Path $stage 'Start.cmd'))
 [void][IO.Directory]::CreateDirectory((Join-Path $repoRoot 'dist'))
 Set-Content -LiteralPath (Join-Path $repoRoot 'dist\last-build.txt') -Value $stage -Encoding UTF8
}finally{$env:GOOS=$oldGOOS;$env:GOARCH=$oldGOARCH;$env:CGO_ENABLED=$oldCGO;Pop-Location}
