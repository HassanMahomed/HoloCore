$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$env:PYTHONPATH = Join-Path $repo 'src'
$sandbox = Join-Path $env:TEMP ('holocore-smoke-' + [guid]::NewGuid().ToString('N'))
$world = Join-Path $sandbox 'world'
$hcHome = Join-Path $sandbox 'home'
$env:HOLOCORE_CONFIG_HOME = Join-Path $sandbox 'config'
try {
    python -m holocore.cli --root $world --json init --home $hcHome | Out-Null
    python -m holocore.cli --root $world --json archive-init | Out-Null
    Set-Content -LiteralPath (Join-Path $world 'sample.py') -Value "def hello():`n    return 'world'" -Encoding utf8
    $atlas = python -m holocore.cli --root $world --json atlas-refresh | ConvertFrom-Json
    python -m holocore.cli --root $world --json remember 'native memory works' --sector project | Out-Null
    $recall = python -m holocore.cli --root $world --json recall 'native memory' --sector project | ConvertFrom-Json
    $paths = python -m holocore.cli --root $world --json paths | ConvertFrom-Json
    $connections = python -m holocore.cli --root $world --json connections | ConvertFrom-Json
    $activity = python -m holocore.cli --root $world --json activity status | ConvertFrom-Json
    $tools = '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | python -m holocore.mcp_server
    if ($atlas.nodes -lt 1) { throw 'Atlas did not map the World' }
    if ($recall.Count -lt 1) { throw 'Animus did not recall the Memory Shard' }
    if ($tools -notmatch 'holocore_archive_search') { throw 'MCP surface missing Archive tools' }
    if ($tools -notmatch 'holocore_activity_handoff') { throw 'MCP surface missing v0.9 activity tools' }
    if (-not $activity.ready) { throw 'Activity ledger is not ready' }
    if ($connections.PSObject.Properties.Name.Count -ne 5) { throw 'Connection health did not inspect five clients' }
    foreach ($path in @('.git','sample.py')) {
        if (-not (Test-Path -LiteralPath (Join-Path $world $path))) { throw "Missing World artifact: $path" }
    }
    foreach ($path in @('Archive','Connections','.codex\config.toml','.mcp.json')) {
        $target = if ($path -in @('.codex\config.toml','.mcp.json')) { Join-Path (Join-Path $hcHome 'Connections') $path } else { Join-Path $hcHome $path }
        if (-not (Test-Path -LiteralPath $target)) { throw "Missing Home artifact: $target" }
    }
    if ($paths.home -ne $hcHome) { throw "CLI reported unexpected Home: $($paths.home)" }
    Write-Output 'HoloCore native installation scenario passed.'
} finally {
    if (Test-Path -LiteralPath $sandbox) { Remove-Item -LiteralPath $sandbox -Recurse -Force }
}
