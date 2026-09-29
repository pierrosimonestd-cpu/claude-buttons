# Removes Claude Buttons: stops the bar, removes the startup shortcut and the MCP server.
# Your buttons and scripts in ~/.claude-buttons are kept unless you pass -RemoveData.
param([switch]$RemoveData)

$bar = Join-Path $PSScriptRoot 'bar.pyw'

Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" |
    Where-Object { $_.CommandLine -like "*$bar*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
Write-Host 'Bar stopped.'

Remove-Item (Join-Path ([Environment]::GetFolderPath('Startup')) 'Claude Buttons.lnk') -ErrorAction SilentlyContinue
Write-Host 'Startup shortcut removed.'

if (Get-Command claude -ErrorAction SilentlyContinue) {
    $ErrorActionPreference = 'Continue'
    claude mcp remove claude-buttons --scope user 2>&1 | Out-Null
    Write-Host 'MCP server removed.'
}

$data = if ($env:CLAUDE_BUTTONS_HOME) { $env:CLAUDE_BUTTONS_HOME } else { Join-Path $HOME '.claude-buttons' }
if ($RemoveData) {
    Remove-Item -Recurse -Force $data -ErrorAction SilentlyContinue
    Write-Host "Data removed: $data"
} else {
    Write-Host "Your buttons are kept in $data (run with -RemoveData to delete them)."
}
