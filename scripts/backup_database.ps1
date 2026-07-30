param(
    [string]$OutputDirectory = "backups"
)

$ErrorActionPreference = "Stop"

function Get-DatabaseUrl {
    if ($env:DATABASE_URL) {
        return $env:DATABASE_URL
    }

    $envFile = Join-Path (Split-Path $PSScriptRoot -Parent) ".env"
    if (-not (Test-Path $envFile)) {
        throw "Не найден DATABASE_URL ни в окружении, ни в файле .env"
    }

    foreach ($line in Get-Content $envFile) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#")) {
            continue
        }
        $parts = $trimmed.Split("=", 2)
        if ($parts.Count -eq 2 -and $parts[0].Trim() -eq "DATABASE_URL") {
            return $parts[1].Trim().Trim('"').Trim("'")
        }
    }

    throw "В .env отсутствует DATABASE_URL"
}

$databaseUrl = Get-DatabaseUrl
$projectRoot = Split-Path $PSScriptRoot -Parent
$outputPath = Join-Path $projectRoot $OutputDirectory
New-Item -ItemType Directory -Path $outputPath -Force | Out-Null

$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$backupFile = Join-Path $outputPath "auction_bot_$timestamp.dump"

& pg_dump `
    --dbname=$databaseUrl `
    --format=custom `
    --no-owner `
    --no-privileges `
    --file=$backupFile

if ($LASTEXITCODE -ne 0) {
    throw "pg_dump завершился с кодом $LASTEXITCODE"
}

Write-Host "Резервная копия создана: $backupFile"
