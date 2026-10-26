$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $MyInvocation.MyCommand.Path
$url = 'http://127.0.0.1:8765/'
$server = $null

try {
    Set-Location -LiteralPath $project

    # Reuse the dashboard if it is already listening on the local port.
    $ready = $false
    try {
        $page = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 2
        $ready = $page.StatusCode -eq 200 -and $page.Content.Contains('ecnplus-token')
    } catch { }

    if (-not $ready) {
        $python = Get-Command py.exe -ErrorAction SilentlyContinue
        $pythonArgs = @('-3')
        if (-not $python) {
            $python = Get-Command python.exe -ErrorAction SilentlyContinue
            $pythonArgs = @()
        }
        if (-not $python) {
            throw 'Python 3.10 or newer was not found. Install it from python.org, then run this file again.'
        }

        $versionText = (& $python.Source @pythonArgs --version 2>&1 | Out-String).Trim()
        $version = [regex]::Match($versionText, 'Python\s+(\d+)\.(\d+)')
        if (-not $version.Success -or ([int]$version.Groups[1].Value -lt 3) -or
            ([int]$version.Groups[1].Value -eq 3 -and [int]$version.Groups[2].Value -lt 10)) {
            throw "Python 3.10 or newer is required. Found: $versionText"
        }

        & $python.Source @pythonArgs -c "from zoneinfo import ZoneInfo; ZoneInfo('Africa/Tunis')" *> $null
        if ($LASTEXITCODE -ne 0) {
            Write-Host 'Installing Tunisia time-zone data (one-time setup)...'
            & $python.Source @pythonArgs -m pip install --user -r (Join-Path $project 'requirements-windows.txt')
            if ($LASTEXITCODE -ne 0) {
                throw 'Could not install the time-zone data. Connect to the internet, then run: py -3 -m pip install --user -r requirements-windows.txt'
            }
        }

        $arguments = $pythonArgs + @('-m', 'ecnplus', 'serve')
        $server = Start-Process -FilePath $python.Source -ArgumentList $arguments `
            -WorkingDirectory $project -PassThru

        $deadline = (Get-Date).AddSeconds(30)
        while ((Get-Date) -lt $deadline) {
            if ($server.HasExited) {
                throw 'The dashboard server stopped during startup. Check the ECN Companion terminal window for details.'
            }
            try {
                $page = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 2
                if ($page.StatusCode -eq 200 -and $page.Content.Contains('ecnplus-token')) {
                    $ready = $true
                    break
                }
            } catch { }
            Start-Sleep -Milliseconds 500
        }
    }

    if (-not $ready) {
        throw 'The dashboard did not respond at http://127.0.0.1:8765 within 30 seconds.'
    }

    $chromeCandidates = @()
    if ($env:ProgramFiles) {
        $chromeCandidates += Join-Path $env:ProgramFiles 'Google\Chrome\Application\chrome.exe'
    }
    if (${env:ProgramFiles(x86)}) {
        $chromeCandidates += Join-Path ${env:ProgramFiles(x86)} 'Google\Chrome\Application\chrome.exe'
    }
    if ($env:LOCALAPPDATA) {
        $chromeCandidates += Join-Path $env:LOCALAPPDATA 'Google\Chrome\Application\chrome.exe'
    }
    $chrome = $chromeCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if ($chrome) {
        Start-Process -FilePath $chrome -ArgumentList $url
    } else {
        Start-Process $url
    }
    Write-Host 'ECN Companion is open. Leave the ECN Companion terminal window running.'
} catch {
    Write-Host ''
    Write-Host $_.Exception.Message -ForegroundColor Red
    Read-Host 'Press Enter to close this window'
    exit 1
}
