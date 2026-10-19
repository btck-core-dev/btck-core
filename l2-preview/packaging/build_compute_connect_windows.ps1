param(
    [Parameter(Mandatory=$true)][string]$PythonExe,
    [string]$OutputDir = "dist\compute-connect-windows"
)
$ErrorActionPreference = "Stop"
$TaskRoot = Split-Path -Parent $PSScriptRoot
$TargetDir = [IO.Path]::GetFullPath((Join-Path $TaskRoot $OutputDir))
$BuildVersion = & $PythonExe -m PyInstaller --version
if ($LASTEXITCODE -ne 0 -or $BuildVersion.Trim() -ne "6.16.0") {
    throw "Use the pinned PyInstaller 6.16.0 build environment"
}
$BasePython = (& $PythonExe -c "import sys; print(sys.base_prefix)").Trim()
$env:TCL_LIBRARY = Join-Path $BasePython "tcl\tcl8.6"
$env:TK_LIBRARY = Join-Path $BasePython "tcl\tk8.6"
& $PythonExe -c "import tkinter; window = tkinter.Tk(); window.withdraw(); window.destroy()"
if ($LASTEXITCODE -ne 0) { throw "The build interpreter must include working Tcl/Tk resources" }
& $PythonExe -m PyInstaller --noconfirm --clean --onefile --windowed `
    --name BTC-K-Compute-Connect --paths (Join-Path $TaskRoot "src") `
    --distpath $TargetDir --workpath (Join-Path $TaskRoot "build\compute-connect-windows") `
    --specpath (Join-Path $TaskRoot "build\compute-connect-windows") `
    (Join-Path $TaskRoot "packaging\compute_connect_entry.py")
if ($LASTEXITCODE -ne 0) { throw "Compute Connect build failed" }
$Artifact = Join-Path $TargetDir "BTC-K-Compute-Connect.exe"
$Digest = (Get-FileHash -Algorithm SHA256 $Artifact).Hash.ToLowerInvariant()
[IO.File]::WriteAllText((Join-Path $TargetDir "SHA256SUMS"), "$Digest  BTC-K-Compute-Connect.exe`n")
Write-Output "Compute Connect executable SHA256: $Digest"
