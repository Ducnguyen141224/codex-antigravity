param([string]$ConfigPath)
. "$PSScriptRoot\common.ps1"
Require-Venv
$ConfigPath = Resolve-SupervisorConfig $ConfigPath
if (-not (Get-Health $ConfigPath)) {
    Write-Output 'Supervisor is already stopped.'
    return
}
Invoke-Checked $VenvPython @('-m', 'codex_agy.cli', '--config', $ConfigPath, 'stop')
for ($Attempt = 0; $Attempt -lt 40; $Attempt++) {
    if (-not (Get-Health $ConfigPath)) { return }
    Start-Sleep -Milliseconds 250
}
throw 'Supervisor is still shutting down. See supervisor.log.'
