param([string]$Package='dist\GameScheduler-Portable')
$ErrorActionPreference='Stop'
$Package=[IO.Path]::GetFullPath($Package)
$name='GameScheduler-Portable-Test-'+[Guid]::NewGuid().ToString('N')
$work=Join-Path ([IO.Path]::GetTempPath()) ('Startup test '+[Guid]::NewGuid().ToString('N'))
$source=Join-Path $work 'Package A\App'
$moved=Join-Path $work 'Package B\App'
[void][IO.Directory]::CreateDirectory($source)
foreach($file in @('Startup.ps1','Portable.ps1','server.exe')){Copy-Item -LiteralPath (Join-Path $Package ('App\'+$file)) -Destination $source}
function Apply-Startup([string]$root,[string]$action){
 $output=& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root 'Startup.ps1') -Action $action -TestTaskName $name
 if($LASTEXITCODE -ne 0){throw "Startup $action failed"}
 return ($output -join "`n" | ConvertFrom-Json)
}
try{
 $v=Apply-Startup $source 'Status';if($v.enabled){throw 'Unexpected existing test registration'}
 $v=Apply-Startup $source 'Enable';if(!$v.enabled -or !$v.current_package){throw 'Startup registration failed'}
 $task=Get-ScheduledTask -TaskName $name
 if($task.Principal.LogonType -ne 'Interactive'){throw 'Startup must use the interactive desktop'}
 if($task.Triggers[0].Delay -ne 'PT30S'){throw 'Logon delay missing'}
 if($task.Actions.Arguments -notlike '*-NoBrowser'){throw 'Unattended logon opens browser'}
 $v=Apply-Startup $source 'Enable'
 if(@(Get-ScheduledTask -TaskName $name).Count -ne 1){throw 'Repeated enable duplicated registration'}
 [void][IO.Directory]::CreateDirectory((Split-Path $moved -Parent))
 Move-Item -LiteralPath $source -Destination $moved
 $v=Apply-Startup $moved 'Status';if($v.current_package){throw 'Relocation mismatch not detected'}
 $v=Apply-Startup $moved 'Enable';if(!$v.current_package){throw 'Relocated startup path not updated'}
 $v=Apply-Startup $moved 'Disable';if($v.enabled){throw 'Startup removal failed'}
 Write-Host 'PASS: startup enable, interactive logon, repeat enable, space paths, relocation detection/update, disable'
}finally{
 Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
 Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
}
