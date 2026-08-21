$proj = "C:\Users\Administrator\coding\telegram-channel-watcher"
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$proj\watchdog.ps1`""
$t1 = New-ScheduledTaskTrigger -Once -At (Get-Date).AddSeconds(30) -RepetitionInterval (New-TimeSpan -Minutes 5)
$t2 = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
Register-ScheduledTask -TaskName "tg-upload-path" -Action $action -Trigger @($t1,$t2) -Settings $settings -Force
Get-ScheduledTask -TaskName "tg-upload-path" | Select-Object TaskName,State | Format-Table -AutoSize
