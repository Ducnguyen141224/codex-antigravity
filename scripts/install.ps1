param([string]$ConfigPath, [string]$PythonExecutable = 'python', [switch]$NoStartup, [switch]$NoCodexRegistration)
. "$PSScriptRoot\common.ps1"

$Python = Get-Command $PythonExecutable -ErrorAction Stop
Invoke-Checked $Python.Source @('-c', 'import sys; assert sys.version_info >= (3,11); print(sys.version.split()[0])')
Invoke-Checked (Get-Command git -ErrorAction Stop).Source @('--version')
if (-not $NoCodexRegistration) { Get-Command codex -ErrorAction Stop | Out-Null }
$ConfigPath = Resolve-SupervisorConfig $ConfigPath

$Uv = Get-Command uv -CommandType Application -ErrorAction SilentlyContinue
if (-not (Test-Path -LiteralPath $VenvPython -PathType Leaf)) {
    if ($Uv -and $Python.Source -like '*WindowsApps*' -and $PythonExecutable -eq 'python') {
        Invoke-Checked $Uv.Source @('venv', '--python', '3.11', '--managed-python', (Join-Path $ProjectRoot '.venv'))
    } else {
        Invoke-Checked $Python.Source @('-m', 'venv', (Join-Path $ProjectRoot '.venv'))
    }
}
$BasePython = & $VenvPython -c 'import sys; print(sys.base_prefix)'
if ($LASTEXITCODE -ne 0 -or $BasePython -like '*WindowsApps*') {
    throw 'This virtualenv uses Microsoft Store Python with AppData redirection. Move .venv aside and rerun using a standalone Python or uv.'
}
if ($Uv) {
    Invoke-Checked $Uv.Source @('pip', 'install', '--python', $VenvPython, '-e', $ProjectRoot)
} else {
    Invoke-Checked $VenvPython @('-m', 'pip', 'install', '-e', $ProjectRoot)
}

if (-not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) {
    [IO.Directory]::CreateDirectory((Split-Path -Parent $ConfigPath)) | Out-Null
    $Template = [IO.File]::ReadAllText((Join-Path $ProjectRoot 'config.example.yaml'))
    $DataPath = (Split-Path -Parent $ConfigPath).Replace("'", "''")
    $Template = $Template.Replace('# data_dir: C:/path/to/CodexAgySupervisor', "data_dir: '$DataPath'")
    $Agy = Get-Command agy -CommandType Application -ErrorAction SilentlyContinue
    if ($Agy) {
        $AgyPath = $Agy.Source.Replace("'", "''")
        $Template = $Template.Replace('agy_executable: agy', "agy_executable: '$AgyPath'")
    }
    [IO.File]::WriteAllText($ConfigPath, $Template)
}
$ConfigPath = & $VenvPython -c 'import sys; from pathlib import Path; print(Path(sys.argv[1]).resolve(strict=True))' $ConfigPath
if ($LASTEXITCODE -ne 0) { throw 'Could not resolve the physical configuration path.' }

$Ready = Get-Health $ConfigPath
if (-not $Ready) {
    Invoke-Checked $VenvPython @('-m', 'codex_agy.cli', '--config', $ConfigPath, 'init')
}
$ProbeOutput = & $VenvPython -m codex_agy.cli --config $ConfigPath probe --session
$ProbeExit = $LASTEXITCODE
$Probe = $ProbeOutput | ConvertFrom-Json
if ($ProbeExit -ne 0) {
    Write-Warning ("ACP probe blocked: " + $Probe.error.message)
} else {
    Write-Output ("AGY " + $Probe.data.agy_version + " | ACP stdio | session verified")
}

if (-not $NoStartup) {
    $User = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    $Pythonw = Join-Path (Split-Path -Parent $VenvPython) 'pythonw.exe'
    if (-not (Test-Path -LiteralPath $Pythonw)) { throw 'The virtualenv lacks pythonw.exe.' }
    $Arguments = "-m codex_agy.cli --config ""$ConfigPath"" serve"
    $Action = New-ScheduledTaskAction -Execute $Pythonw -Argument $Arguments -WorkingDirectory $ProjectRoot
    $Trigger = New-ScheduledTaskTrigger -AtLogOn -User $User
    $Principal = New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Limited
    $Options = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero)
    $Registration = @{
        TaskName = $TaskName
        Action = $Action
        Trigger = $Trigger
        Principal = $Principal
        Settings = $Options
        Force = $true
    }
    $Existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $Existing -or $Existing.State -ne 'Running') {
        Register-ScheduledTask @Registration | Out-Null
    }
}
& "$PSScriptRoot\start_supervisor.ps1" -ConfigPath $ConfigPath
& "$PSScriptRoot\smoke_test.ps1" -ConfigPath $ConfigPath -IpcOnly
if (-not $NoCodexRegistration) {
    & "$PSScriptRoot\install_plugin.ps1" -ConfigPath $ConfigPath
}
Write-Output "Config: $ConfigPath"
if (-not $NoCodexRegistration) {
    Write-Output 'In a new local Codex chat, select Antigravity with @ or explicitly ask to use it.'
}
