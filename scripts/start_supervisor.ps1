param([string]$ConfigPath)
. "$PSScriptRoot\common.ps1"
Require-Venv
$ConfigPath = Resolve-SupervisorConfig $ConfigPath
$Ready = Get-Health $ConfigPath
if ($Ready) {
    $Ready | ConvertTo-Json -Compress
    return
}

$LogDir = & $VenvPython -c 'import sys; from codex_agy.config import load_settings; print(load_settings(sys.argv[1]).data_dir)' $ConfigPath
if ($LASTEXITCODE -ne 0) { throw 'Could not load the supervisor configuration.' }
[IO.Directory]::CreateDirectory($LogDir) | Out-Null
$LaunchArgs = "-m codex_agy.cli --config ""$ConfigPath"" serve"
$StdoutLog = Join-Path $LogDir 'supervisor.out.log'
$ErrorLog = Join-Path $LogDir 'supervisor.launch.log'
$Launch = @{
    FilePath = $VenvPython
    ArgumentList = $LaunchArgs
    WorkingDirectory = $ProjectRoot
    WindowStyle = 'Hidden'
    RedirectStandardOutput = $StdoutLog
    RedirectStandardError = $ErrorLog
    PassThru = $true
}
$Child = Start-Process @Launch
for ($Attempt = 0; $Attempt -lt 40; $Attempt++) {
    $Ready = Get-Health $ConfigPath
    if ($Ready) {
        $Ready | ConvertTo-Json -Compress
        return
    }
    if ($Child.HasExited) { break }
    Start-Sleep -Milliseconds 300
}
throw "Supervisor did not start. See $(Join-Path $LogDir 'supervisor.log')."
