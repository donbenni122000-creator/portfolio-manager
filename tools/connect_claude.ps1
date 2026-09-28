# Adds Portfolio Manager to Claude Desktop as a local MCP server ("portfolio-manager").
# Keeps every other setting, and saves a backup next to the config file first.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root
$log = Join-Path $root 'data\claude_connect.log'
New-Item -ItemType Directory -Force (Join-Path $root 'data') | Out-Null
function Log($m) { Write-Host "  $m"; Add-Content -Path $log -Value $m }
Set-Content -Path $log -Value "Run $(Get-Date -Format s) PS $($PSVersionTable.PSVersion)"
try {

    $py  = Join-Path $root '.venv\Scripts\python.exe'
    $srv = Join-Path $root 'mcp_server.py'
    if (-not (Test-Path $py)) { Log 'ERROR: open Portfolio Manager once first (it sets up Python).'; Read-Host '  Press Enter'; exit 1 }

    # 1. make sure the MCP package is installed and the server imports cleanly
    #    (native tools write warnings to stderr; don't let PowerShell treat those as fatal)
    $ErrorActionPreference = 'Continue'
    Log 'installing packages...'
    & $py -m pip install -q --disable-pip-version-check -r requirements.txt 2>&1 | Out-Null
    $check = (& $py -c "import sys; sys.path.insert(0, r'$root'); from app.mcp_server import build_server; s=build_server(); print('OK')" 2>&1 | Out-String).Trim()
    $ErrorActionPreference = 'Stop'
    Log "server check: $check"
    if ("$check" -notmatch 'OK') { Log 'ERROR: MCP server failed to load.'; Read-Host '  Press Enter'; exit 1 }

    # 2. update every Claude Desktop config location that exists (standard + Microsoft Store install)
    $dirs = @(Join-Path $env:APPDATA 'Claude')
    Get-ChildItem (Join-Path $env:LOCALAPPDATA 'Packages') -Directory -Filter 'Claude_*' -ErrorAction SilentlyContinue |
        ForEach-Object { $d = Join-Path $_.FullName 'LocalCache\Roaming\Claude'; if (Test-Path $d) { $dirs += $d } }

    foreach ($dir in $dirs) {
        New-Item -ItemType Directory -Force $dir | Out-Null
        $cfg = Join-Path $dir 'claude_desktop_config.json'
        $obj = [pscustomobject]@{}
        if (Test-Path $cfg) {
            Copy-Item $cfg "$cfg.bak-portfolio" -Force
            $raw = Get-Content $cfg -Raw
            if ($raw -and $raw.Trim()) { $obj = $raw | ConvertFrom-Json }
        }
        if (-not $obj.PSObject.Properties['mcpServers']) {
            $obj | Add-Member -NotePropertyName 'mcpServers' -NotePropertyValue ([pscustomobject]@{})
        }
        $entry = [pscustomobject]@{ command = $py; args = @($srv) }
        if ($obj.mcpServers.PSObject.Properties['portfolio-manager']) { $obj.mcpServers.'portfolio-manager' = $entry }
        else { $obj.mcpServers | Add-Member -NotePropertyName 'portfolio-manager' -NotePropertyValue $entry }
        $json = $obj | ConvertTo-Json -Depth 50
        [IO.File]::WriteAllText($cfg, $json, (New-Object Text.UTF8Encoding $false))
        $null = Get-Content $cfg -Raw | ConvertFrom-Json          # verify it is valid JSON
        Log "updated: $cfg"
    }
    Log 'DONE'
    Write-Host ''
    Write-Host '  Portfolio Manager is now connected to Claude Desktop.' -ForegroundColor Green
    Write-Host '  Fully quit Claude (tray icon > Quit) and open it again to load it.'
    Start-Sleep -Seconds 6
} catch {
    Log "ERROR: $($_.Exception.Message) at line $($_.InvocationInfo.ScriptLineNumber): $($_.InvocationInfo.Line.Trim())"
    Read-Host '  Press Enter to close'
    exit 1
}
