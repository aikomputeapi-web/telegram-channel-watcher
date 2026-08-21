param()
$proj = "C:\Users\Administrator\coding\telegram-channel-watcher"
$log = "$proj\.logs\watchdog.log"
while ($true) {
    try { & "$proj\start-upload-path.ps1" } catch { Add-Content $log "$(Get-Date -Format o) ERROR: $_" }
    Start-Sleep -Seconds 300
}
