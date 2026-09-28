# Opens a secure Cloudflare tunnel so ChatGPT can reach your Portfolio Manager,
# and copies the connector URL to your clipboard.
# Only the secret MCP path is reachable through the tunnel - the web UI and API are blocked for outside requests.
# Close this window to disconnect ChatGPT.

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root
$Host.UI.RawUI.WindowTitle = 'Portfolio Manager - ChatGPT connection'

function Test-App {
    try { return (Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 'http://127.0.0.1:8000/api/config').StatusCode -eq 200 }
    catch { return $false }
}

Write-Host ''
Write-Host '  Portfolio Manager -> ChatGPT' -ForegroundColor Cyan
Write-Host '  ----------------------------'

# 1. make sure the app is running
if (-not (Test-App)) {
    Write-Host '  Starting Portfolio Manager...'
    Start-Process wscript.exe -ArgumentList "`"$root\Portfolio Manager.vbs`""
    for ($i = 0; $i -lt 90 -and -not (Test-App); $i++) { Start-Sleep -Seconds 1 }
}
if (-not (Test-App)) { Write-Host '  Portfolio Manager did not start. See data\server.log' -ForegroundColor Red; Read-Host '  Press Enter to close'; exit 1 }

$tokenFile = Join-Path $root 'data\mcp_token.txt'
if (-not (Test-Path $tokenFile)) { Write-Host '  Missing data\mcp_token.txt - restart Portfolio Manager and try again.' -ForegroundColor Red; Read-Host; exit 1 }
$token = (Get-Content $tokenFile -Raw).Trim()

# 2. cloudflared (Cloudflare's official tunnel client) - downloaded once into tools\
$cf = Join-Path $root 'tools\cloudflared.exe'
if (-not (Test-Path $cf)) {
    Write-Host '  Downloading cloudflared from Cloudflare (one time, ~60 MB)...'
    $ProgressPreference = 'SilentlyContinue'
    Invoke-WebRequest -UseBasicParsing -OutFile $cf `
        'https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe'
}

# 3. start the tunnel and read its public address
$log = Join-Path $root 'data\tunnel.log'
Remove-Item $log -ErrorAction SilentlyContinue
$proc = Start-Process $cf -ArgumentList 'tunnel --no-autoupdate --url http://127.0.0.1:8000' `
        -RedirectStandardError $log -NoNewWindow -PassThru
$url = $null
for ($i = 0; $i -lt 60 -and -not $url; $i++) {
    Start-Sleep -Seconds 1
    if (Test-Path $log) {
        $m = Select-String -Path $log -Pattern 'https://[a-z0-9-]+\.trycloudflare\.com' | Select-Object -First 1
        if ($m) { $url = $m.Matches[0].Value }
    }
}
if (-not $url) { Write-Host '  Could not open the tunnel. See data\tunnel.log' -ForegroundColor Red; Read-Host; exit 1 }

$connector = "$url/connect/$token/mcp"
Set-Content -Path (Join-Path $root 'data\chatgpt_connector_url.txt') -Value $connector
Set-Clipboard -Value $connector

Write-Host ''
Write-Host '  Connected. Your ChatGPT connector URL (already copied to the clipboard):' -ForegroundColor Green
Write-Host ''
Write-Host "  $connector" -ForegroundColor Yellow
Write-Host ''
Write-Host '  In ChatGPT: Settings > Apps & Connectors > Advanced > turn on Developer mode,'
Write-Host '  then Create (new connector) > paste the URL > Authentication: No authentication > Create.'
Write-Host '  (The secret in the URL is what protects your app - do not share it.)'
Write-Host ''
Write-Host '  Note: this address changes each time you run this. Update the connector URL in ChatGPT if so.'
Write-Host '  Keep this window open while you use ChatGPT. Close it to disconnect.' -ForegroundColor Cyan
Wait-Process -Id $proc.Id
