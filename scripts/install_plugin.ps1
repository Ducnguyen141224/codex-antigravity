param([string]$ConfigPath)
. "$PSScriptRoot\common.ps1"
Require-Venv

$Codex = Get-Command codex -ErrorAction Stop
$ConfigPath = Resolve-SupervisorConfig $ConfigPath
$ConfigPath = & $VenvPython -c 'import sys; from pathlib import Path; print(Path(sys.argv[1]).resolve(strict=True))' $ConfigPath
if ($LASTEXITCODE -ne 0) { throw 'Could not resolve the Supervisor config. Run scripts\install.ps1 first.' }
$RuntimePath = Join-Path $ProjectRoot 'plugins\antigravity\skills\agy\runtime.json'
$Runtime = [ordered]@{
    python = $VenvPython
    config = $ConfigPath
    start_script = Join-Path $PSScriptRoot 'start_supervisor.ps1'
} | ConvertTo-Json
[IO.File]::WriteAllText($RuntimePath, $Runtime)

Invoke-Checked $Codex.Source @('plugin', 'marketplace', 'add', $ProjectRoot)
Invoke-Checked $Codex.Source @('plugin', 'add', 'antigravity@agy-local')

Remove-PreviousCodexMcp $Codex.Source
