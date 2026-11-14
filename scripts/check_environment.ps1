param([string]$ConfigPath, [switch]$VerifySession)
. "$PSScriptRoot\common.ps1"

$Python = Get-Command python -ErrorAction Stop
Invoke-Checked $Python.Source @('-c', 'import sys; assert sys.version_info >= (3,11); print(sys.version.split()[0])')
Invoke-Checked (Get-Command git -ErrorAction Stop).Source @('--version')
$Agy = Get-Command agy -CommandType Application -ErrorAction SilentlyContinue
if (Test-Path -LiteralPath $VenvPython) {
    $ProbeArgs = @('-m', 'codex_agy.cli')
    if ($ConfigPath) { $ProbeArgs += @('--config', (Resolve-SupervisorConfig $ConfigPath)) }
    $ProbeArgs += 'probe'
    if ($VerifySession) { $ProbeArgs += '--session' }
    Invoke-Checked $VenvPython $ProbeArgs
} elseif ($Agy) {
    Invoke-Checked $Agy.Source @('--version')
    $HelpText = (& $Agy.Source acp --help 2>&1 | Out-String)
    if ($LASTEXITCODE -ne 0 -or $HelpText -notmatch 'ACP.*stdio') {
        throw 'The installed AGY has no ACP stdio. Run agy update.'
    }
    Write-Output 'AGY advertises ACP stdio. Run install.ps1 to verify the handshake.'
} else {
    throw 'AGY CLI was not found. Install it from the official Google Antigravity site.'
}
