param(
    [string]$Source = ".env",
    [string]$OutputDirectory = "."
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $Source -PathType Leaf)) {
    throw "Source env file not found: $Source"
}

$providerKeys = @{
    "PAYSTACK_SECRET_KEY" = "paystack"
    "PAYSTACK_WEBHOOK_SECRET" = "paystack"
    "PAYSTACK_BASE_URL" = "paystack"
    "PAYSTACK_ENVIRONMENT" = "paystack"
    "PAWAPAY_API_TOKEN" = "pawapay"
    "PAWAPAY_BASE_URL" = "pawapay"
    "PAWAPAY_CALLBACK_URL" = "pawapay"
    "PAWAPAY_PAYOUT_CALLBACK_URL" = "pawapay"
    "PAWAPAY_DEPOSIT_CALLBACK_URL" = "pawapay"
    "PAWAPAY_REFUND_CALLBACK_URL" = "pawapay"
    "PAWAPAY_CALLBACK_SIGNATURE_REQUIRED" = "pawapay"
    "PAWAPAY_PUBLIC_KEYS_URL" = "pawapay"
    "CINETPAY_API_KEY" = "cinetpay"
    "CINETPAY_API_PASSWORD" = "cinetpay"
    "CINETPAY_BASE_URL" = "cinetpay"
    "CINETPAY_COUNTRY" = "cinetpay"
    "JEKO_API_KEY" = "jeko"
    "JEKO_API_KEY_ID" = "jeko"
    "JEKO_STORE_ID" = "jeko"
    "JEKO_BASE_URL" = "jeko"
    "JEKO_WEBHOOK_SECRET" = "jeko"
}

$lines = Get-Content -LiteralPath $Source
$general = [System.Collections.Generic.List[string]]::new()
$providers = @{}
foreach ($name in @("paystack", "pawapay", "cinetpay", "jeko", "sandbox")) {
    $providers[$name] = [System.Collections.Generic.List[string]]::new()
}

foreach ($line in $lines) {
    if ($line -match '^\s*(?:#|$)') {
        continue
    }
    if ($line -notmatch '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=') {
        continue
    }
    $key = $Matches[1]
    if ($key -in @("PAYMENT_GATEWAY_MODE", "PAYMENT_PROCESSOR_MODE", "PAYMENT_GATEWAY_AUTOCONFIRM")) {
        continue
    }
    if ($providerKeys.ContainsKey($key)) {
        $providers[$providerKeys[$key]].Add($line)
    } else {
        $general.Add($line)
    }
}

$providerModes = @{
    "paystack" = @("paystack", "paystack")
    "pawapay" = @("pawapay", "pawapay")
    "cinetpay" = @("stub", "cinetpay")
    "jeko" = @("stub", "jeko")
    "sandbox" = @("stub", "sandbox")
}
foreach ($name in $providers.Keys) {
    $gatewayMode, $processorMode = $providerModes[$name]
    $providers[$name].Insert(0, "PAYMENT_GATEWAY_AUTOCONFIRM=false")
    $providers[$name].Insert(0, "PAYMENT_PROCESSOR_MODE=$processorMode")
    $providers[$name].Insert(0, "PAYMENT_GATEWAY_MODE=$gatewayMode")
}

New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
$generalPath = Join-Path $OutputDirectory ".env.general"
$general | Set-Content -LiteralPath $generalPath -Encoding utf8
foreach ($name in $providers.Keys) {
    $path = Join-Path $OutputDirectory ".env.$name"
    $providers[$name] | Set-Content -LiteralPath $path -Encoding utf8
}

Write-Output "Generated .env.general and provider profiles in $OutputDirectory"
Write-Output "No secret values were printed. Generated files are ignored by Git."
