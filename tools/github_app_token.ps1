# FlightDVR Studio - browse, trim and convert HDZero goggle DVR footage.
# Copyright (C) 2026 Isadu Nkemi
#
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later
# version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along with
# this program. If not, see <https://www.gnu.org/licenses/>.

param(
    [string] $AppId = (& git config --local --get flightdvr.githubAppId),
    [string] $InstallationId = (& git config --local --get flightdvr.githubAppInstallationId),
    [string] $PrivateKeyPath = (& git config --local --get flightdvr.githubAppKeyPath)
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function ConvertTo-Base64Url {
    param([byte[]] $Bytes)

    $base64 = [Convert]::ToBase64String($Bytes).TrimEnd([char] '=')
    return $base64.Replace('+', '-').Replace('/', '_')
}

foreach ($setting in @{
        "flightdvr.githubAppId" = $AppId
        "flightdvr.githubAppInstallationId" = $InstallationId
        "flightdvr.githubAppKeyPath" = $PrivateKeyPath
    }.GetEnumerator()) {
    if ([string]::IsNullOrWhiteSpace($setting.Value)) {
        throw "Missing local git setting $($setting.Key)."
    }
}

if (-not (Test-Path -LiteralPath $PrivateKeyPath -PathType Leaf)) {
    throw "GitHub App private key not found at the configured path."
}

$utf8 = [Text.Encoding]::UTF8
$issuedAt = [DateTimeOffset]::UtcNow.AddSeconds(-60).ToUnixTimeSeconds()
$expiresAt = [DateTimeOffset]::UtcNow.AddMinutes(9).ToUnixTimeSeconds()
$header = ConvertTo-Base64Url $utf8.GetBytes('{"alg":"RS256","typ":"JWT"}')
$payloadJson = @{
    iat = $issuedAt
    exp = $expiresAt
    iss = [long] $AppId
} | ConvertTo-Json -Compress
$payload = ConvertTo-Base64Url $utf8.GetBytes($payloadJson)
$unsignedToken = "$header.$payload"

$rsa = [Security.Cryptography.RSA]::Create()
try {
    $rsa.ImportFromPem((Get-Content -Raw -LiteralPath $PrivateKeyPath))
    $signatureBytes = $rsa.SignData(
        $utf8.GetBytes($unsignedToken),
        [Security.Cryptography.HashAlgorithmName]::SHA256,
        [Security.Cryptography.RSASignaturePadding]::Pkcs1
    )
} finally {
    $rsa.Dispose()
}

$jwt = "$unsignedToken.$(ConvertTo-Base64Url $signatureBytes)"
$headers = @{
    Accept = "application/vnd.github+json"
    Authorization = "Bearer $jwt"
    "X-GitHub-Api-Version" = "2022-11-28"
    "User-Agent" = "FlightDVR-Studio-assistant"
}
$response = Invoke-RestMethod `
    -Uri "https://api.github.com/app/installations/$InstallationId/access_tokens" `
    -Method Post `
    -Headers $headers `
    -ContentType "application/json" `
    -Body '{}'

if ([string]::IsNullOrWhiteSpace($response.token)) {
    throw "GitHub did not return an installation token."
}

Write-Output $response.token
