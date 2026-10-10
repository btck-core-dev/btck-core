@echo off
REM ==============================================================================
REM ⚡ BTC-K Symbiotic Worker: Windows 1-Click Fast Launcher
REM Zero-Key Architecture: Pearl (PRL) Baseline Mining + DeepSeek SatK AI Grid
REM ==============================================================================
chcp 65001 > nul
title BTC-K Symbiotic Worker (Pearl + DeepSeek AI)
color 0A

echo ================================================================
echo  ⚡ BTC-K Symbiotic Worker: Windows 1-Click Launcher
echo     Pearl (PRL) Baseline PoW + DeepSeek AI Compute Symbiosis
echo ================================================================

IF "%1"=="" (
    set /p BTCK_PAYOUT_ADDR="Enter your BTC-K Stealth Payout Address (btck1s...): "
) ELSE (
    set BTCK_PAYOUT_ADDR=%1
)

IF "%BTCK_PAYOUT_ADDR%"=="" (
    echo [!] Address cannot be empty.
    pause
    exit /b 1
)

echo [✓] Zero-Key Address Configured: %BTCK_PAYOUT_ADDR%

REM Probe NVIDIA GPU
where nvidia-smi >nul 2>nul
IF %ERRORLEVEL% EQU 0 (
    echo [*] Probing GPU with nvidia-smi...
    nvidia-smi --query-gpu=name,memory.total --format=csv,noheader
) ELSE (
    echo [!] nvidia-smi not found. Ensure NVIDIA drivers are installed.
)

REM Launch Python Worker
where python >nul 2>nul
IF %ERRORLEVEL% NEQ 0 (
    echo [!] Python 3 is required. Please install Python 3.10+ from python.org.
    pause
    exit /b 1
)

echo [*] Starting BTC-K Symbiotic Worker Daemon...
python -c "import sys, os; sys.path.insert(0, os.path.abspath('src')); from worker.symbiotic_worker import SymbioticWorkerConfig, SymbioticWorker; from worker.tui_console import TUIConsole; cfg=SymbioticWorkerConfig(worker_address='%BTCK_PAYOUT_ADDR%'); w=SymbioticWorker(cfg); c=TUIConsole('%BTCK_PAYOUT_ADDR%', w.profile.assigned_tier.value, w.profile.tier_level, w.profile.gpu_models[0] if w.profile.gpu_models else 'GPU', w.profile.total_vram_gb, w.profile.recommended_model); c.print_dashboard()"

pause
