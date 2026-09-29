# Installs Claude Buttons: installs the Python dependency, registers the MCP server with
# Claude Code and starts the bar at every Windows sign-in (it stays hidden until Claude is open).
param([switch]$NoStartup)

$ErrorActionPreference = 'Stop'
$dir = $PSScriptRoot
$bar = Join-Path $dir 'bar.pyw'

function Fail($msg) { Write-Host "Error: $msg" -ForegroundColor Red; exit 1 }

# 1. Requirements
$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCmd) { Fail 'Python 3.10+ not found on PATH. Install it from https://www.python.org/downloads/' }
$python = $pythonCmd.Source
& $python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"
if ($LASTEXITCODE -ne 0) { Fail 'Python 3.10 or newer is required.' }
& $python -c "import tkinter" 2>$null
if ($LASTEXITCODE -ne 0) { Fail 'tkinter is missing: reinstall Python with the "tcl/tk and IDLE" option.' }
if (-not (Get-Command claude -ErrorAction SilentlyContinue)) {
    Fail 'Claude Code CLI ("claude") not found on PATH. See https://code.claude.com/docs'
}
$pythonw = Join-Path (Split-Path $python) 'pythonw.exe'
if (-not (Test-Path $pythonw)) { $pythonw = $python }

# 2. Python dependency
Write-Host 'Installing Python dependencies...'
& $python -m pip install --quiet -r (Join-Path $dir 'requirements.txt')
if ($LASTEXITCODE -ne 0) { Fail 'pip install failed.' }

# 3. MCP server, available in every Claude Code session
$ErrorActionPreference = 'Continue'
claude mcp remove claude-buttons --scope user 2>&1 | Out-Null  # reinstall: ignore if missing
$ErrorActionPreference = 'Stop'
claude mcp add claude-buttons --scope user -- $python (Join-Path $dir 'mcp_server.py')
if ($LASTEXITCODE -ne 0) { Fail 'could not register the MCP server.' }
Write-Host 'MCP server registered (user scope).'

# 4. Stop previous instances
Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" |
    Where-Object { $_.CommandLine -like "*$bar*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }

# 5. Start with Windows
$lnk = Join-Path ([Environment]::GetFolderPath('Startup')) 'Claude Buttons.lnk'
if ($NoStartup) {
    Remove-Item $lnk -ErrorAction SilentlyContinue
} else {
    $sc = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk)
    $sc.TargetPath = $pythonw
    $sc.Arguments = '"' + $bar + '"'
    $sc.WorkingDirectory = $dir
    $sc.Save()
    Write-Host "Starts with Windows: $lnk"
}

# 6. Start the bar
Start-Process $pythonw -ArgumentList ('"' + $bar + '"') -WorkingDirectory $dir
Write-Host ''
Write-Host 'Done. Look for the tab on the right edge of the Claude window, or press Ctrl+Alt+B.' -ForegroundColor Green
Write-Host 'Then ask Claude Code: "make me a button that ..."'
