# Kill all Desktop Calendar processes and relaunch from repo root.
$ErrorActionPreference = "SilentlyContinue"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

Get-CimInstance Win32_Process |
    Where-Object {
        $_.Name -match '^(python\.exe|pythonw\.exe)$' -and
        $_.CommandLine -and
        ($_.CommandLine -match [regex]::Escape($Root) -or $_.CommandLine -match 'desktopcalendar') -and
        ($_.CommandLine -match 'main\.py')
    } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }

Get-Process -Name "DesktopCalendar" -ErrorAction SilentlyContinue |
    ForEach-Object { Stop-Process -Id $_.Id -Force }

Start-Sleep -Milliseconds 600
Start-Process -FilePath "python" -ArgumentList "main.py" -WorkingDirectory $Root -WindowStyle Hidden
Write-Output "restarted"
