param(
    [string]$DbmsPath = "$env:USERPROFILE\.Neo4jDesktop2\Data\dbmss\dbms-a3fb0f08-fe8e-4ceb-8f2a-228522586a6f",
    [string]$Drive = "N:",
    [string]$JavaHome = "$env:USERPROFILE\.Neo4jDesktop2\Cache\runtime\zulu21.50.19-ca-jre21.0.11-win_x64"
)

# Run this only if you agree to Neo4j's Enterprise evaluation terms.
# Terms are shown by Neo4j when the database fails with "LICENSE AGREEMENT REQUIRED".

$ErrorActionPreference = "Stop"

subst $Drive /D 2>$null
subst $Drive $DbmsPath

$env:JAVA_HOME = $JavaHome
$env:NEO4J_HOME = "$Drive/"
$env:NEO4J_CONF = "$Drive/conf"

$java = Join-Path $JavaHome "bin\java.exe"
& $java -cp "$Drive/lib/*" "-Dbasedir=$Drive/" org.neo4j.server.startup.Neo4jAdminCommand server license --accept-evaluation

