# Force TLS 1.2 (Required for GitHub/CDN)
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

# Bypass SSL Certificate Checks (In case of corporate proxy/firewall)
if (-not ([System.Management.Automation.PSTypeName]'TrustAllCertsPolicy').Type) {
    add-type @"
    using System.Net;
    using System.Security.Cryptography.X509Certificates;
    public class TrustAllCertsPolicy : ICertificatePolicy {
        public bool CheckValidationResult(
            ServicePoint srvPoint, X509Certificate certificate,
            WebRequest request, int certificateProblem) {
            return true;
        }
    }
"@
    [System.Net.ServicePointManager]::CertificatePolicy = New-Object TrustAllCertsPolicy
}

$targetDir = ".\ut_vfx\resources\web\univer\lib"
$baseUrl = "https://unpkg.com/@univerjs/presets@0.1.0-beta.2"

# Files to download
$files = @{
    "univer.full.js" = "$baseUrl/lib/umd/index.js";
    "univer.css"     = "$baseUrl/lib/styles/index.css"
}

Write-Host "Downloading to: $targetDir"
if (-not (Test-Path $targetDir)) { New-Item -ItemType Directory -Force -Path $targetDir }

foreach ($name in $files.Keys) {
    $url = $files[$name]
    $dest = Join-Path $targetDir $name
    Write-Host "Downloading $name from $url..."
    
    try {
        Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing
        Write-Host "SUCCESS: $name"
    }
    catch {
        Write-Host "ERROR downloading $name : $_"
        Write-Host "URL: $url"
    }
}
