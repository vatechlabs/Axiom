$ErrorActionPreference = "Stop"

$agent = Join-Path $PSScriptRoot "agent.py"
$python = (Get-Command pythonw.exe -ErrorAction Stop).Source
$startup = [Environment]::GetFolderPath("Startup")
$shortcutPath = Join-Path $startup "Axiom Folder Agent.lnk"

# Register automatic startup on Windows login
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $python
$shortcut.Arguments = "`"$agent`" --watch --interval 30"
$shortcut.WorkingDirectory = $PSScriptRoot
$shortcut.Description = "Axiom automatic Windows folder synchronization"
$shortcut.Save()

# Start agent now if not already running
$running = Get-CimInstance Win32_Process |
    Where-Object {
        $_.Name -match "^pythonw?\.exe$" -and
        $_.CommandLine -like "*agent.py*--watch*"
    }

if (-not $running) {
    Start-Process -FilePath $python `
        -ArgumentList "`"$agent`" --watch --interval 30" `
        -WorkingDirectory $PSScriptRoot
}

Write-Host "AXIOM AGENT AUTO-START CONFIGURED"
Write-Host "Startup shortcut: $shortcutPath"
