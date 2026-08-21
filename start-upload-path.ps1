param()
$ErrorActionPreference = "Continue"
$proj = "C:\Users\Administrator\coding\telegram-channel-watcher"
$logs = "$proj\.logs"
$py = "$proj\.venv\Scripts\python.exe"
$cf = "C:\Program Files (x86)\cloudflared\cloudflared.exe"

# Load deployment secrets (gitignored). Falls back to environment variables.
if (Test-Path "$proj\deploy-secrets.ps1") { . "$proj\deploy-secrets.ps1" }
$key = $env:TG_WATCHER_KEY
if (-not $key) { $key = $TG_WATCHER_KEY }
$vps = $env:TG_WATCHER_VPS
if (-not $vps) { $vps = $TG_WATCHER_VPS }
if (-not $key -or -not $vps) {
    throw "TG_WATCHER_KEY and TG_WATCHER_VPS are not configured (deploy-secrets.ps1 or env vars)"
}
$urlFile = "$logs\tunnel-url.txt"
$watchLog = "$logs\watchdog.log"
$pulse = Get-Date -Format o

function Log([string]$msg) {
    Add-Content -Path $watchLog -Value "$pulse $msg"
}

function Ensure-Receiver {
    $l = Get-NetTCPConnection -LocalPort 8787 -State Listen -ErrorAction SilentlyContinue
    if (-not $l) {
        Log "receiver down - starting"
        Start-Process -FilePath $py -ArgumentList '-u', "$proj\upload_receiver.py", '--port', '8787' `
            -WorkingDirectory $proj -WindowStyle Hidden `
            -RedirectStandardOutput "$logs\receiver.out.log" -RedirectStandardError "$logs\receiver.err.log"
        Start-Sleep -Seconds 3
        $l = Get-NetTCPConnection -LocalPort 8787 -State Listen -ErrorAction SilentlyContinue
        if ($l) { Log "receiver up (pid $($l[0].OwningProcess))" } else { Log "receiver FAILED to start" }
    }
}

$permanentUrl = "https://tg-uploads.nowrouter.store"
$configPath = "$env:USERPROFILE\.cloudflared\config.yml"

function Ensure-Tunnel {
    $p = Get-Process cloudflared -ErrorAction SilentlyContinue
    if (-not $p) {
        Log "cloudflared down - starting named tunnel"
        Start-Process -FilePath $cf -ArgumentList 'tunnel', '--config', $configPath, 'run', 'tg-uploads' `
            -WindowStyle Hidden `
            -RedirectStandardOutput "$logs\cloudflared.out.log" -RedirectStandardError "$logs\cloudflared.err.log"
        Start-Sleep -Seconds 15
        $p = Get-Process cloudflared -ErrorAction SilentlyContinue
        if ($p) { Log "cloudflared up (pid $($p.Id))" } else { Log "cloudflared FAILED to start" }
    } else {
        Log "cloudflared running (pid $($p.Id))"
    }
}

function Ensure-SearchTunnel {
    $l = Get-NetTCPConnection -LocalPort 5000 -State Listen -ErrorAction SilentlyContinue
    if (-not $l) {
        Log "search tunnel down - starting SSH tunnel"
        Start-Process -FilePath "ssh.exe" -ArgumentList @(
            "-o", "BatchMode=yes", "-o", "ServerAliveInterval=30",
            "-o", "ExitOnForwardFailure=yes", "-i", $key,
            "-L", "5000:127.0.0.1:5000", "-N", $vps
        ) -WindowStyle Hidden
        Start-Sleep -Seconds 5
        $l = Get-NetTCPConnection -LocalPort 5000 -State Listen -ErrorAction SilentlyContinue
        if ($l) { Log "search tunnel up (pid $($l[0].OwningProcess))" } else { Log "search tunnel FAILED" }
    }
}

Ensure-Receiver
Ensure-Tunnel
Ensure-SearchTunnel
