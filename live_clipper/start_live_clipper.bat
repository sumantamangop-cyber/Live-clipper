@echo off
rem START: runs watch and serve now and at every logon, restarting them if they stop.
rem Stop them with stop_live_clipper.bat.
set "BAT=%~dp0keep_running.bat"
set "DIR=%~dp0.."
powershell -NoProfile -Command "foreach ($m in 'watch','serve') { $action = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument ('/c \"' + $env:BAT + '\" ' + $m) -WorkingDirectory $env:DIR; $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME; $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero); Register-ScheduledTask -TaskName ('LiveClipper ' + $m) -Action $action -Trigger $trigger -Settings $settings -Description ('Live clipper ' + $m) -Force | Out-Null; Enable-ScheduledTask -TaskName ('LiveClipper ' + $m) | Out-Null; Start-ScheduledTask -TaskName ('LiveClipper ' + $m) }; 'Live clipper started. Stop it with stop_live_clipper.bat.'"
