param(
    [string]$Drive = "N:"
)

$ErrorActionPreference = "SilentlyContinue"

$connections = netstat -ano | Select-String ":7474|:7687"
$pids = @()
foreach ($line in $connections) {
    $parts = -split $line.ToString().Trim()
    if ($parts.Length -gt 0) {
        $pids += $parts[-1]
    }
}

$pids | Sort-Object -Unique | ForEach-Object {
    Stop-Process -Id ([int]$_) -Force
}

subst $Drive /D 2>$null
Write-Host "Neo4j stopped if it was running."

