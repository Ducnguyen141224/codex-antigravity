param([string]$ConfigPath, [switch]$IpcOnly)
. "$PSScriptRoot\common.ps1"
Require-Venv
$ConfigPath = Resolve-SupervisorConfig $ConfigPath
Invoke-Checked $VenvPython @('-m', 'codex_agy.cli', '--config', $ConfigPath, 'health')
if (-not $IpcOnly) {
    Invoke-Checked $VenvPython @('-m', 'codex_agy.cli', '--config', $ConfigPath, 'probe', '--session')
}
