param(
    [Parameter(Mandatory=$true)][string]$ConfigPath,
    [string]$PythonPath,
    [string]$ExecutablePath,
    [string]$InstallRoot = 'C:\ProgramData\BTCK-Compute',
    [string]$TaskName = 'BTCK-Compute-Node',
    [switch]$Start
)
$ErrorActionPreference = 'Stop'
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { throw 'Run this installer from an elevated PowerShell window.' }
if ([bool]$PythonPath -eq [bool]$ExecutablePath) { throw 'Choose either ExecutablePath or PythonPath.' }
foreach ($value in @($ConfigPath, $PythonPath, $ExecutablePath, $InstallRoot)) {
    if ($value -match '["\r\n]') { throw 'Unsupported path characters.' }
}
if ($TaskName -notmatch '^[A-Za-z0-9_. -]{1,100}$') { throw 'Use a simple explicit task name.' }
$ConfigPath = (Resolve-Path -LiteralPath $ConfigPath).Path
if ($PythonPath) { $PythonPath = (Resolve-Path -LiteralPath $PythonPath).Path }
if ($ExecutablePath) { $ExecutablePath = (Resolve-Path -LiteralPath $ExecutablePath).Path }
$source = Split-Path $PSScriptRoot -Parent
if ((Test-Path -LiteralPath $InstallRoot) -or (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue)) {
    throw 'Installation exists. Preserve it and use an explicit versioned upgrade.'
}
if ($ExecutablePath) {
    $checked = Start-Process -FilePath $ExecutablePath -ArgumentList ('--node-config "' + $ConfigPath + '" --check') -Wait -PassThru
    if ($checked.ExitCode -ne 0) { throw 'Configuration validation failed. Inspect compute-node.log beside the selected config.' }
} else {
    & $PythonPath "$source\scripts\l2_compute_node.py" --config $ConfigPath --check
    if ($LASTEXITCODE -ne 0) { throw 'Configuration validation failed.' }
}
$identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
New-Item -ItemType Directory -Path $InstallRoot | Out-Null
& icacls.exe $InstallRoot /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' "${identity}:(OI)(CI)F" | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Cannot protect the installation directory.' }
foreach ($directory in @('scripts', 'src\worker', 'state')) {
    New-Item -ItemType Directory -Path "$InstallRoot\$directory" -Force | Out-Null
}
if ($ExecutablePath) {
    Copy-Item -LiteralPath $ExecutablePath -Destination "$InstallRoot\BTC-K-Compute-Connect.exe"
} else {
    foreach ($name in @('__init__.py','resident_worker.py','agent_protocol.py','mining_guard.py','mining_lease.py','compute_node.py')) {
        Copy-Item -LiteralPath "$source\src\worker\$name" -Destination "$InstallRoot\src\worker\$name"
    }
    Copy-Item -LiteralPath "$source\scripts\l2_compute_node.py" -Destination "$InstallRoot\scripts\l2_compute_node.py"
}
$config = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
$config | Add-Member -Force NoteProperty status_file "$InstallRoot\state\status.json"
$config | Add-Member -Force NoteProperty mining_lease_file "$InstallRoot\state\mining-lease.json"
$config | ConvertTo-Json -Depth 30 | Set-Content -LiteralPath "$InstallRoot\node.json" -Encoding UTF8
if ($ExecutablePath) {
    $action = New-ScheduledTaskAction -Execute "$InstallRoot\BTC-K-Compute-Connect.exe" -Argument ('--node-config "' + $InstallRoot + '\node.json"') -WorkingDirectory $InstallRoot
} else {
    $arguments = '"' + $InstallRoot + '\scripts\l2_compute_node.py" --config "' + $InstallRoot + '\node.json" --log-file "' + $InstallRoot + '\node.log"'
    $action = New-ScheduledTaskAction -Execute $PythonPath -Argument $arguments -WorkingDirectory $InstallRoot
}
$boot = New-ScheduledTaskTrigger -AtStartup
$boot.Delay = 'PT30S'
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType S4U -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $boot -Principal $principal -Settings $settings -Description 'BTC-K resident compute node; development credits; bounded Pearl pause and automatic recovery.' | Out-Null
if ($Start) { Start-ScheduledTask -TaskName $TaskName }
Write-Output ('Compute node installed with an elevated startup task. Started: ' + [bool]$Start)
