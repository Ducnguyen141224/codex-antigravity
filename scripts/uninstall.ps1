param([string]$ConfigPath)
. "$PSScriptRoot\common.ps1"

$ConfigPath = Resolve-SupervisorConfig $ConfigPath
if (Test-Path -LiteralPath $VenvPython -PathType Leaf) {
    & "$PSScriptRoot\stop_supervisor.ps1" -ConfigPath $ConfigPath
}
$Scheduled = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($Scheduled) { Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false }
$Codex = Get-Command codex -ErrorAction SilentlyContinue
if ($Codex) {
    $InstalledPlugins = & $Codex.Source plugin list --json
    if ($LASTEXITCODE -eq 0 -and
        (($InstalledPlugins | ConvertFrom-Json).installed.pluginId -contains 'antigravity@agy-local')) {
        Invoke-Checked $Codex.Source @('plugin', 'remove', 'antigravity@agy-local')
    }
    $Marketplaces = & $Codex.Source plugin marketplace list
    if ($LASTEXITCODE -eq 0 -and ($Marketplaces -match '^agy-local\s')) {
        Invoke-Checked $Codex.Source @('plugin', 'marketplace', 'remove', 'agy-local')
    }
    Remove-PreviousCodexMcp $Codex.Source
}
Write-Output 'Unregistered the launcher and Codex integration. The virtualenv, database, and Git worktrees are retained.'
