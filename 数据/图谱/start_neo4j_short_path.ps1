param(
    [string]$DbmsPath = "$env:USERPROFILE\.Neo4jDesktop2\Data\dbmss\dbms-a3fb0f08-fe8e-4ceb-8f2a-228522586a6f",
    [string]$Drive = "N:",
    [string]$JavaHome = "$env:USERPROFILE\.Neo4jDesktop2\Cache\runtime\zulu21.50.19-ca-jre21.0.11-win_x64"
)

$ErrorActionPreference = "Stop"

subst $Drive /D 2>$null
subst $Drive $DbmsPath

$env:JAVA_HOME = $JavaHome
$env:NEO4J_HOME = "$Drive/"
$env:NEO4J_CONF = "$Drive/conf"

$java = Join-Path $JavaHome "bin\java.exe"
$stdout = "$Drive/logs/manual-console.out.log"
$stderr = "$Drive/logs/manual-console.err.log"

if (Test-Path $stdout) { Remove-Item $stdout -Force }
if (Test-Path $stderr) { Remove-Item $stderr -Force }

$process = Start-Process `
    -FilePath $java `
    -ArgumentList @("-cp", "$Drive/lib/*", "-Dbasedir=$Drive/", "org.neo4j.server.startup.Neo4jCommand", "console") `
    -WorkingDirectory "$Drive/" `
    -WindowStyle Hidden `
    -RedirectStandardOutput $stdout `
    -RedirectStandardError $stderr `
    -PassThru

Start-Sleep -Seconds 8

Write-Host "Neo4j process PID: $($process.Id)"
Write-Host "HTTP: http://localhost:7474"
Write-Host "Bolt: bolt://localhost:7687"
netstat -ano | Select-String ":7474|:7687"

if ($process.HasExited) {
    Write-Host ""
    Write-Host "Neo4j exited quickly. Last log lines:"
    if (Test-Path $stdout) { Get-Content -Tail 80 -Encoding UTF8 $stdout }
    if (Test-Path $stderr) { Get-Content -Tail 80 -Encoding UTF8 $stderr }
}

