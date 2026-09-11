<#
    Slate - first run setup.

    Everything Slate needs that is not source code lives in setup/components.json.
    This script reads that file, downloads what is missing, and puts it where the
    application looks for it.

    Three rules it holds to:

      nothing system-wide     no registry, no PATH, no installer. Everything
                              lands under this folder and deleting the folder
                              removes it.
      safe to run twice       every component is probed before it is fetched,
                              so a second run does nothing and an interrupted
                              first run resumes where it stopped.
      no secrets in git       the database password is asked for once and
                              written to slate/config.json, which is ignored
                              by git and stays on this machine.

    Called by setup.bat. Run it directly if you want the switches:

        -Check      say what would happen, download nothing
        -Force      re-fetch even what is already in place
        -Server     also install PostgreSQL (the Central Server machine only)
        -SkipConfig leave slate/config.json alone
#>

[CmdletBinding()]
param(
    [string] $Root = ".",
    [switch] $Check,
    [switch] $Force,
    [switch] $Server,
    [switch] $SkipConfig
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path $Root).Path

# TLS 1.2 - Windows PowerShell 5.1 still defaults low enough that github.com
# refuses the handshake, which surfaces as a baffling "could not create SSL
# channel" rather than anything about protocols.
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$script:Failures = @()

# --------------------------------------------------------------------- output

function Say      { param($m) Write-Host "  $m" }
function Step     { param($m) Write-Host ""; Write-Host "==> $m" -ForegroundColor Cyan }
function Good     { param($m) Write-Host "  [ok] $m" -ForegroundColor Green }
function Skip     { param($m) Write-Host "  [--] $m" -ForegroundColor DarkGray }
function Warn     { param($m) Write-Host "  [!!] $m" -ForegroundColor Yellow }
function Bad      { param($m) Write-Host "  [XX] $m" -ForegroundColor Red; $script:Failures += $m }

function Banner {
    Write-Host ""
    Write-Host "  SLATE" -ForegroundColor Cyan -NoNewline
    Write-Host "   studio pipeline setup"
    Write-Host "  $Root" -ForegroundColor DarkGray
    if ($Check) { Write-Host "  dry run - nothing will be downloaded" -ForegroundColor Yellow }
    Write-Host ""
}

# ---------------------------------------------------------------- downloading

function Get-File {
    <#
        Download to a temporary name and rename on success, so an interrupted
        download never leaves a half file that the next run mistakes for a
        finished one.
    #>
    param([string] $Url, [string] $Target)

    $partial = "$Target.partial"
    if (Test-Path $partial) { Remove-Item $partial -Force }
    New-Item -ItemType Directory -Force -Path (Split-Path $Target) | Out-Null

    $started = Get-Date
    try {
        # Invoke-WebRequest buffers the whole body in memory before writing,
        # which is fine for a script but miserable for a 300MB archive. The
        # WebClient streams it.
        $client = New-Object System.Net.WebClient
        $client.Headers.Add("User-Agent", "Slate-Setup")
        $client.DownloadFile($Url, $partial)
    }
    catch {
        if (Test-Path $partial) { Remove-Item $partial -Force }
        throw "download failed: $($_.Exception.Message)"
    }
    finally {
        if ($client) { $client.Dispose() }
    }

    Move-Item $partial $Target -Force
    $mb   = [Math]::Round((Get-Item $Target).Length / 1MB, 1)
    $secs = [Math]::Round(((Get-Date) - $started).TotalSeconds, 1)
    Say "$mb MB in $secs s"
}

function Expand-Into {
    param([string] $Archive, [string] $Target)
    New-Item -ItemType Directory -Force -Path $Target | Out-Null
    # Expand-Archive refuses to overwrite without -Force, and -Force on a
    # partially extracted folder is exactly what a resumed run needs.
    Expand-Archive -LiteralPath $Archive -DestinationPath $Target -Force
}

# ---------------------------------------------------------------- components

function Resolve-Url {
    <#
        Some projects put the build's commit hash in the asset filename, so a
        pinned link stops working within the week. Those entries name the
        release instead and the real asset is looked up here.
    #>
    param($Component)

    if ($Component.url) { return $Component.url }
    if (-not $Component.resolve) { return $null }

    $repo = $Component.resolve.github_release
    $tag  = $Component.resolve.tag
    $api  = "https://api.github.com/repos/$repo/releases/tags/$tag"

    $release = Invoke-RestMethod -Uri $api -Headers @{ "User-Agent" = "Slate-Setup" } -TimeoutSec 30
    $asset = $release.assets | Where-Object { $_.name -match $Component.resolve.asset_match } | Select-Object -First 1
    if (-not $asset) { throw "no asset in $repo@$tag matching $($Component.resolve.asset_match)" }
    Say "resolved to $($asset.name)"
    return $asset.browser_download_url
}

function Install-Component {
    param($Component)

    $name  = $Component.name
    $title = $Component.title
    $probe = Join-Path $Root $Component.probe

    # An optional component that cannot be fetched is reported and stepped
    # over. Slate runs without Olive or OpenRV; it does not run without Python.
    $soft = ($Component.required -eq $false)

    if ($name -eq "postgresql" -and -not $Server) {
        Skip "$title - workstation install, skipping (use /server on the server machine)"
        return
    }

    if ((Test-Path $probe) -and -not $Force) {
        Good "$title - already in place"
        return
    }

    if ($Component.kind -eq "manual") {
        Warn "$title - not installed"
        Say  $Component.why
        Say  "Expected at: $($Component.probe)"
        return
    }

    Step "$title"
    Say  $Component.why

    try   { $url = Resolve-Url $Component }
    catch { if ($soft) { Warn "$title - skipped ($($_.Exception.Message))" } else { Bad "$title - $($_.Exception.Message)" }; return }
    if (-not $url) { Warn "$title - no download link"; return }

    if ($Check) {
        try {
            $head = Invoke-WebRequest -Uri $url -Method Head -TimeoutSec 30 -UseBasicParsing
            $mb = [Math]::Round([int64]$head.Headers.'Content-Length' / 1MB, 1)
            Good "would download $mb MB"
        } catch {
            if ($soft) { Warn "$title - not reachable, and optional ($($_.Exception.Message))" }
            else       { Bad  "$title - the download link is not reachable ($($_.Exception.Message))" }
        }
        return
    }

    $cache = Join-Path $Root "runtime\_downloads"
    $file  = Join-Path $cache (Split-Path $url -Leaf)

    try {
        if ((Test-Path $file) -and -not $Force) { Say "using the copy already downloaded" }
        else { Get-File -Url $url -Target $file }

        switch ($Component.kind) {
            "file" {
                $dest = Join-Path $Root $Component.dest
                New-Item -ItemType Directory -Force -Path (Split-Path $dest) | Out-Null
                Copy-Item $file $dest -Force
            }
            "zip" {
                $staging = Join-Path $cache "_x_$name"
                if (Test-Path $staging) { Remove-Item $staging -Recurse -Force }
                Expand-Into -Archive $file -Target $staging

                if ($Component.install_to) {
                    # One file out of a large archive - ffmpeg's build carries
                    # documentation and libraries Slate does not use.
                    $found = Get-ChildItem $staging -Recurse -File |
                             Where-Object { $_.Name -eq (Split-Path $Component.install_to -Leaf) } |
                             Select-Object -First 1
                    if (-not $found) { throw "could not find $($Component.install_to) inside the archive" }
                    $dest = Join-Path $Root $Component.install_to
                    New-Item -ItemType Directory -Force -Path (Split-Path $dest) | Out-Null
                    Copy-Item $found.FullName $dest -Force
                }
                else {
                    $dest = Join-Path $Root $Component.dest
                    # Most archives wrap everything in one top folder. Unwrap it
                    # so paths do not end up doubled (pgsql/pgsql/bin).
                    $items = @(Get-ChildItem $staging)
                    $source = if ($items.Count -eq 1 -and $items[0].PSIsContainer) { $items[0].FullName } else { $staging }
                    if ($Component.into) { $dest = Join-Path $dest $Component.into }
                    New-Item -ItemType Directory -Force -Path $dest | Out-Null
                    Copy-Item (Join-Path $source "*") $dest -Recurse -Force
                }
                Remove-Item $staging -Recurse -Force
            }
        }

        if (Test-Path (Join-Path $Root $Component.probe)) { Good "$title installed" }
        elseif ($soft) { Warn "$title - unpacked but $($Component.probe) is not there; Slate will run without it" }
        else { Bad "$title - installed but $($Component.probe) is not there" }
    }
    catch {
        if ($soft) { Warn "$title - skipped ($($_.Exception.Message))" }
        else       { Bad  "$title - $($_.Exception.Message)" }
    }
}

# ------------------------------------------------------------------- python

function Initialize-Python {
    <#
        The embeddable build ships deliberately crippled: no pip, and a ._pth
        file that switches off site-packages so installed libraries are
        invisible. Both are fixed here, once.
    #>
    $py = Join-Path $Root "runtime\python\python.exe"
    if (-not (Test-Path $py)) { Bad "Python is not installed - nothing else can proceed"; return $false }

    $pth = Get-ChildItem (Join-Path $Root "runtime\python") -Filter "python*._pth" | Select-Object -First 1
    if ($pth) {
        $body = Get-Content $pth.FullName -Raw
        if ($body -match "(?m)^\s*#\s*import\s+site") {
            Step "Turning on site-packages"
            ($body -replace "(?m)^\s*#\s*(import\s+site)", '$1') | Set-Content $pth.FullName -Encoding ASCII
            Good "python._pth patched"
        }
    }

    $pip = Join-Path $Root "runtime\python\Scripts\pip.exe"
    if (-not (Test-Path $pip) -or $Force) {
        Step "Installing pip"
        $getpip = Join-Path $Root "runtime\python\get-pip.py"
        if (-not (Test-Path $getpip)) { Bad "get-pip.py is missing"; return $false }
        & $py $getpip --no-warn-script-location 2>&1 | ForEach-Object { Say $_ }
        if (Test-Path $pip) { Good "pip installed" } else { Bad "pip did not install"; return $false }
    } else { Good "pip - already in place" }

    return $true
}

function Install-Requirements {
    $py  = Join-Path $Root "runtime\python\python.exe"
    $req = Join-Path $Root "requirements.txt"
    if (-not (Test-Path $req)) { Warn "no requirements.txt - skipping"; return }

    Step "Installing Slate's dependencies"
    Say "PySide6 and the rest. This is the slow part - a few minutes on a first run."
    & $py -m pip install --disable-pip-version-check --no-warn-script-location -r $req 2>&1 |
        ForEach-Object { if ($_ -match "^(Successfully|ERROR|Collecting PySide6)") { Say $_ } }

    & $py -c "import PySide6" 2>$null
    if ($LASTEXITCODE -eq 0) { Good "dependencies installed" }
    else { Bad "PySide6 will not import - Slate cannot start without it" }
}

# -------------------------------------------------------------------- config

function Write-LocalConfig {
    <#
        The one file that holds a password, and the one file git never sees.

        It is written rather than committed because this repository is public.
        The value itself does not change - it is the studio's existing database
        password, typed once per machine.
    #>
    $target = Join-Path $Root "slate\config.json"

    if ((Test-Path $target) -and -not $Force) {
        Good "slate\config.json - already set up (delete it to start over)"
        return
    }

    Step "Studio connection"
    Say "Slate needs to know where the studio database is. Leave a line blank to"
    Say "accept the default in brackets. Nothing here is ever committed."
    Write-Host ""

    $host_    = Read-Host "  Database host  [localhost]"
    if (-not $host_) { $host_ = "localhost" }
    $port     = Read-Host "  Port           [5440]"
    if (-not $port)  { $port = "5440" }
    $dbname   = Read-Host "  Database name  [slate]"
    if (-not $dbname) { $dbname = "slate" }
    $dbuser   = Read-Host "  Database user  [ut_vfx_app]"
    if (-not $dbuser) { $dbuser = "ut_vfx_app" }

    $secure   = Read-Host "  Database password" -AsSecureString
    $dbpass   = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
                    [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure))

    $secure2  = Read-Host "  Admin password (for Slate's own admin panel)" -AsSecureString
    $adminpw  = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
                    [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure2))

    $config = [ordered]@{
        "_comment"   = "Written by setup.bat. Local to this machine and ignored by git - do not commit it."
        "db_mode"    = if ($dbpass) { "postgres" } else { "sqlite" }
        "db_host"    = $host_
        "db_port"    = [int]$port
        "db_name"    = $dbname
        "db_user"    = $dbuser
        "db_password"    = $dbpass
        "admin_password" = $adminpw
    }

    New-Item -ItemType Directory -Force -Path (Split-Path $target) | Out-Null
    $config | ConvertTo-Json -Depth 4 | Set-Content $target -Encoding UTF8
    Good "wrote slate\config.json"

    if (-not $dbpass) {
        Warn "No password given, so Slate will start on its local SQLite fallback."
        Say  "That works for trying it out. Re-run setup.bat when the server is ready."
    }
}

# ------------------------------------------------------------------ launchers

function Write-Launchers {
    Step "Launchers"
    $shortcuts = @{
        "Slate.bat"        = "slate\vfx_studio_main.py"
        "Slate Ops.bat"    = "slate\studio_ops_main.py"
        "Slate Server.bat" = "slate_server\main.py"
    }
    foreach ($name in $shortcuts.Keys) {
        $body = @"
@echo off
cd /d "%~dp0"
"%~dp0runtime\python\python.exe" "$($shortcuts[$name])" %*
if errorlevel 1 pause
"@
        Set-Content (Join-Path $Root $name) $body -Encoding ASCII
    }
    Good "Slate.bat, Slate Ops.bat, Slate Server.bat"
}

# ----------------------------------------------------------------------- main

Banner

$manifestPath = Join-Path $Root "setup\components.json"
if (-not (Test-Path $manifestPath)) {
    Bad "setup\components.json is missing - this is not a complete checkout"
    exit 1
}
$manifest = Get-Content $manifestPath -Raw | ConvertFrom-Json

foreach ($component in $manifest.components) { Install-Component $component }

if (-not $Check) {
    if (Initialize-Python) { Install-Requirements }
    if (-not $SkipConfig) { Write-LocalConfig }
    Write-Launchers
}

Write-Host ""
if ($script:Failures.Count -gt 0) {
    Write-Host "Finished with $($script:Failures.Count) problem(s):" -ForegroundColor Red
    foreach ($f in $script:Failures) { Write-Host "  - $f" -ForegroundColor Red }
    exit 1
}

if ($Check) {
    Write-Host "Dry run finished. Everything above is reachable." -ForegroundColor Green
} else {
    Write-Host "Slate is set up." -ForegroundColor Green
    Write-Host ""
    Write-Host "  Double-click Slate.bat to start."
    Write-Host "  The studio guide is in docs\guide - start with docs\guide\artists.md."
}
exit 0
