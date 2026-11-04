# NeoCP one-click installer for Windows (run via "Install NeoCP (Windows).bat")
$ErrorActionPreference = "Stop"
$Version = "__VERSION__"
$Repo = "__REPO__"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host "  NeoCP $Version - AI co-producer for Cubase" -ForegroundColor Cyan
Write-Host "==============================================" -ForegroundColor Cyan

$uvBin = Join-Path $env:USERPROFILE ".local\bin"
$env:Path = "$uvBin;$env:Path"
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "-> Installing uv (Python manager, no admin rights needed)..."
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    $env:Path = "$uvBin;$env:Path"
}

$wheel = Get-ChildItem -Path $Here -Filter "neocp-*.whl" -ErrorAction SilentlyContinue | Select-Object -First 1
if ($wheel) { $src = $wheel.FullName } else { $src = "https://github.com/$Repo/releases/download/v$Version/neocp-$Version-py3-none-any.whl" }
Write-Host "-> Installing NeoCP ($src)..."
uv tool install --python 3.12 --force "$src"

# loopMIDI provides the virtual MIDI ports Windows lacks
$loopmidi = Test-Path "${env:ProgramFiles(x86)}\Tobias Erichsen\loopMIDI\loopMIDI.exe"
if (-not $loopmidi) { $loopmidi = Test-Path "$env:ProgramFiles\Tobias Erichsen\loopMIDI\loopMIDI.exe" }
if (-not $loopmidi) {
    $ans = Read-Host "loopMIDI (free virtual MIDI ports) is required. Install it now with winget? [Y/n]"
    if ($ans -ne "n") {
        try { winget install --id TobiasErichsen.loopMIDI -e --accept-package-agreements --accept-source-agreements }
        catch { Write-Host "Could not run winget. Download it from https://www.tobias-erichsen.de/software/loopmidi.html" -ForegroundColor Yellow }
    }
}

Write-Host "-> Installing the Cubase MIDI Remote script and configuring Claude..."
$listen = @()
$names = (Get-CimInstance Win32_SoundDevice -ErrorAction SilentlyContinue | ForEach-Object { $_.Name }) -join ";"
if ($names -match "VoiceMeeter") { $listen = @("--listen-device", "Voicemeeter Out B1") }
elseif ($names -match "VB-Audio Virtual Cable|CABLE") { $listen = @("--listen-device", "CABLE Output") }
& (Join-Path $uvBin "neocp-install.exe") @listen

Write-Host ""
Write-Host "IMPORTANT: open loopMIDI and create these 3 ports (type the name, press +):" -ForegroundColor Yellow
Write-Host "   NeoCP to Cubase" -ForegroundColor Yellow
Write-Host "   NeoCP from Cubase" -ForegroundColor Yellow
Write-Host "   NeoCP Notes" -ForegroundColor Yellow
Write-Host "   (enable 'Autostart loopMIDI' in its tray menu)"
Write-Host ""
Write-Host "To let Claude LISTEN to Cubase see docs\CONFIGURAR_CUBASE.md section 6 (VoiceMeeter / VB-Cable / interface loopback)."
Write-Host "Done! Restart Claude Desktop and (re)start Cubase."
Read-Host "Press Enter to close"
