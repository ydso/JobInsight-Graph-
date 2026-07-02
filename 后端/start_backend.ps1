param(
    [int]$Port = 8000
)

Set-Location $PSScriptRoot

if (-not (Test-Path ".env") -and -not $env:NEO4J_PASSWORD) {
    Write-Warning "NEO4J_PASSWORD is not configured. Copy .env.example to .env and set the password before using graph APIs."
}

python -m uvicorn app.main:app --host 127.0.0.1 --port $Port --reload
