$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$VenvPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$TaskName = 'Codex AGY Supervisor'
$DefaultData = Join-Path $env:LOCALAPPDATA 'CodexAgySupervisor'
$DefaultConfig = Join-Path $DefaultData 'config.yaml'

function Resolve-SupervisorConfig([string]$ConfigPath) {
    if (-not $ConfigPath) { $ConfigPath = $DefaultConfig }
    return [IO.Path]::GetFullPath($ConfigPath)
}

function Invoke-Checked {
    param([string]$Executable, [string[]]$Arguments)
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed ($LASTEXITCODE): $Executable"
    }
}

function Require-Venv {
    if (-not (Test-Path -LiteralPath $VenvPython -PathType Leaf)) {
        throw 'Run scripts\install.ps1 first.'
    }
}

function Remove-PreviousCodexMcp([string]$CodexExecutable) {
    $ErrorActionPreference = 'Continue'
    $Previous = & $CodexExecutable mcp get codex_agy --json 2>$null
    if ($LASTEXITCODE -eq 0) {
        $Transport = ($Previous | ConvertFrom-Json).transport
        if ($Transport.type -eq 'stdio' -and $Transport.command -eq $VenvPython -and
            $Transport.args -contains 'codex_agy.cli') {
            Invoke-Checked $CodexExecutable @('mcp', 'remove', 'codex_agy')
        }
    }
}

function Get-Health([string]$ConfigPath) {
    $Output = & $VenvPython -m codex_agy.cli --config $ConfigPath health
    if ($LASTEXITCODE -eq 0) { return ($Output | ConvertFrom-Json).data }
}
