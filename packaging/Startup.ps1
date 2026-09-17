param(
 [ValidateSet('Status','Enable','Disable')][string]$Action='Status',
 [switch]$Elevated,
 [string]$UserSid='',
 [string]$TestTaskName=''
)
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=New-Object Text.UTF8Encoding($false)
$identity=[Security.Principal.WindowsIdentity]::GetCurrent()
$currentSid=$identity.User.Value
if($UserSid -and $UserSid -ne $currentSid){throw 'Use the same Windows account for startup setup and game automation.'}
$UserSid=$currentSid
$isAdmin=([Security.Principal.WindowsPrincipal]$identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
$name='GameScheduler-Portable-'+$UserSid
if($TestTaskName){if($TestTaskName -notmatch '^GameScheduler-Portable-Test-[a-zA-Z0-9-]+$'){throw 'Invalid test task name'};$name=$TestTaskName}
$launcher=Join-Path $PSScriptRoot 'Portable.ps1'
if(!(Test-Path -LiteralPath $launcher) -or !(Test-Path -LiteralPath (Join-Path $PSScriptRoot 'server.exe'))){throw 'Startup setup must run from a built portable App directory.'}
$existing=Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
if($existing -and $existing.Description -ne 'Game Scheduler portable startup'){throw 'A different task already uses the startup name.'}
if($Action -ne 'Status'){
 $needsAdmin=($Action -eq 'Enable' -and $Elevated) -or ($existing -and $existing.Principal.RunLevel -eq 'Highest')
 if($needsAdmin -and !$isAdmin){
  function Quote-Literal([string]$s){return "'"+$s.Replace("'","''")+"'"}
  $command='& '+(Quote-Literal $PSCommandPath)+' -Action '+$Action+' -UserSid '+(Quote-Literal $UserSid)
  if($Elevated){$command+=' -Elevated'}
  if($TestTaskName){$command+=' -TestTaskName '+(Quote-Literal $TestTaskName)}
  $command+='; if (!$?) { exit 1 }'
  $encoded=[Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
  $child=Start-Process -FilePath (Join-Path $PSHOME 'powershell.exe') -Verb RunAs -Wait -PassThru -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-EncodedCommand',$encoded)
  if($child.ExitCode -ne 0){throw 'Startup setup was cancelled or failed. No success was recorded.'}
 }elseif($Action -eq 'Enable'){
  $exe=Join-Path $PSHOME 'powershell.exe'
  $launchArguments='-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "'+$launcher+'" -Action Start -NoBrowser'
  $taskAction=New-ScheduledTaskAction -Execute $exe -Argument $launchArguments -WorkingDirectory $PSScriptRoot
  $trigger=New-ScheduledTaskTrigger -AtLogOn -User $UserSid
  $trigger.Delay='PT30S'
  $level='Limited';if($Elevated){$level='Highest'}
  $principal=New-ScheduledTaskPrincipal -UserId $UserSid -LogonType Interactive -RunLevel $level
  $settings=New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero)
  Register-ScheduledTask -TaskName $name -Action $taskAction -Trigger $trigger -Principal $principal -Settings $settings -Description 'Game Scheduler portable startup' -Force | Out-Null
 }elseif($existing){Unregister-ScheduledTask -TaskName $name -Confirm:$false}
}
$existing=Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
$matchesPackage=$false
if($existing){$matchesPackage=$existing.Actions.Arguments -eq ('-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "'+$launcher+'" -Action Start -NoBrowser')}
[ordered]@{supported=$true;enabled=($null -ne $existing -and $existing.State -ne 'Disabled');current_package=[bool]$matchesPackage;elevated=($null -ne $existing -and $existing.Principal.RunLevel -eq 'Highest');task_name=$name;app_directory=$PSScriptRoot;registered_arguments=$(if($existing){$existing.Actions.Arguments}else{''})} | ConvertTo-Json -Compress
