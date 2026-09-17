param([Parameter(ValueFromRemainingArguments=$true)][string[]]$GoArgs=@('version'))
$ErrorActionPreference='Stop'
if($env:OS -ne 'Windows_NT'){throw 'This bootstrap requires Windows.'}
if(![Environment]::Is64BitOperatingSystem){throw 'This package requires 64-bit Windows.'}
$repoRoot=Split-Path $PSScriptRoot -Parent
$version='1.26.8'
# Official Windows x64 archive and SHA256: https://go.dev/dl/#go1.26.8
$expectedHash='b92c3b2adae85a11ba71fe7216daf0d84e82af4c8ab6c5625807f28622043a59'
$toolsRoot=Join-Path $repoRoot 'Toolchain'
$install=Join-Path $toolsRoot "go$version-windows-amd64"
$goRoot=Join-Path $install 'go'
$goExe=Join-Path $goRoot 'bin\go.exe'
$downloadDir=Join-Path $toolsRoot 'downloads'
$archive=Join-Path $downloadDir "go$version.windows-amd64.zip"
[void][IO.Directory]::CreateDirectory($downloadDir)
$lock=$null;$unpack=$null
$oldProtocol=[Net.ServicePointManager]::SecurityProtocol
$oldProgress=$ProgressPreference
try{
 # A concurrent setup must not see a partially extracted toolchain.
 $lock=[IO.File]::Open((Join-Path $toolsRoot 'setup.lock'),'OpenOrCreate','ReadWrite','None')
 if(!(Test-Path -LiteralPath $goExe)){
  if(Test-Path -LiteralPath $install){throw "Incomplete toolchain at $install. Rename that folder and retry."}
  if(!(Test-Path -LiteralPath $archive)){
   Write-Host "Downloading portable Go $version into $downloadDir ..."
   [Net.ServicePointManager]::SecurityProtocol=$oldProtocol -bor [Net.SecurityProtocolType]::Tls12
   $ProgressPreference='SilentlyContinue'
   $partial=$archive+'.partial'
   try{
    Invoke-WebRequest -UseBasicParsing -Uri "https://go.dev/dl/go$version.windows-amd64.zip" -OutFile $partial -TimeoutSec 600
    if((Get-FileHash -LiteralPath $partial -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expectedHash){throw 'Go download SHA256 mismatch; nothing was installed.'}
    Move-Item -LiteralPath $partial -Destination $archive
   }finally{if(Test-Path -LiteralPath $partial){Remove-Item -LiteralPath $partial -Force}}
  }
  if((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expectedHash){throw "Cached Go ZIP failed SHA256 verification. Remove $archive and retry."}
  Write-Host 'Extracting verified Go archive ...'
  $unpack=Join-Path $toolsRoot ('extract-'+[Guid]::NewGuid().ToString('N'))
  Expand-Archive -LiteralPath $archive -DestinationPath $unpack
  if(!(Test-Path -LiteralPath (Join-Path $unpack 'go\bin\go.exe'))){throw 'Go archive has no compiler.'}
  Move-Item -LiteralPath $unpack -Destination $install
  $unpack=$null
 }
}finally{
 if($unpack -and (Test-Path -LiteralPath $unpack)){Remove-Item -LiteralPath $unpack -Recurse -Force}
 if($lock){$lock.Dispose()}
 [Net.ServicePointManager]::SecurityProtocol=$oldProtocol
 $ProgressPreference=$oldProgress
}
$settings=@{
 GOROOT=$goRoot
 GOPATH=(Join-Path $toolsRoot 'gopath')
 GOMODCACHE=(Join-Path $toolsRoot 'cache\modules')
 GOCACHE=(Join-Path $toolsRoot 'cache\build')
 GOTMPDIR=(Join-Path $toolsRoot 'tmp')
 TEMP=(Join-Path $toolsRoot 'tmp')
 TMP=(Join-Path $toolsRoot 'tmp')
 GOBIN=(Join-Path $toolsRoot 'bin')
 GOENV='off'
 GOTOOLCHAIN='local'
 APPDATA=(Join-Path $toolsRoot 'profile\AppData\Roaming')
 LOCALAPPDATA=(Join-Path $toolsRoot 'profile\AppData\Local')
 GOWORK='off'
 GOFLAGS=''
}
$saved=@{}
foreach($key in $settings.Keys){$saved[$key]=[Environment]::GetEnvironmentVariable($key,'Process')}
try{
 foreach($key in @('GOPATH','GOMODCACHE','GOCACHE','GOTMPDIR','GOBIN','APPDATA','LOCALAPPDATA')){[void][IO.Directory]::CreateDirectory($settings[$key])}
 foreach($key in $settings.Keys){[Environment]::SetEnvironmentVariable($key,$settings[$key],'Process')}
 & $goExe telemetry off
 if($LASTEXITCODE -ne 0){throw 'Could not disable Go telemetry in the repository-local profile.'}
 $actual=& $goExe version
 if($LASTEXITCODE -ne 0 -or $actual -ne "go version go$version windows/amd64"){throw "Unexpected portable compiler: $actual"}
 & $goExe @GoArgs
 if($LASTEXITCODE -ne 0){throw "Portable Go failed with exit code $LASTEXITCODE"}
}finally{
 foreach($key in $settings.Keys){[Environment]::SetEnvironmentVariable($key,$saved[$key],'Process')}
}
