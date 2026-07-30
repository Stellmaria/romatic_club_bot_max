param(
    [Parameter(Mandatory = $true)]
    [string]$BackupFile
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

if (-not (Test-Path $BackupFile)) {
    throw "Файл резервной копии не найден: $BackupFile"
}

$databaseUrl = Get-DatabaseUrl

& pg_restore `
    --dbname=$databaseUrl `
    --clean `
    --if-exists `
    --no-owner `
    --no-privileges `
    --exit-on-error `
    $BackupFile

if ($LASTEXITCODE -ne 0) {
    throw "pg_restore завершился с кодом $LASTEXITCODE"
}

Write-Host "База восстановлена. Теперь выполни: python -m db.migrator"
