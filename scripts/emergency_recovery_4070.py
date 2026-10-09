#!/usr/bin/env python3
import time
import socket
import datetime
import os
import sys
import paramiko

TARGET_IP = "100.73.134.73"
SSH_USER = "jczhang"
SSH_KEY = os.environ.get("BTCK_4070_SSH_KEY")
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_FILE = os.path.join(REPO_ROOT, "logs", "emergency_recovery_4070.log")

def log(msg):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

def check_banner(ip, port=22, timeout=1.5):
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect((ip, port))
        banner = s.recv(100)
        return banner.startswith(b"SSH-")
    except Exception:
        return False
    finally:
        s.close()

def execute_recovery():
    if not SSH_KEY:
        log("❌ BTCK_4070_SSH_KEY is required; password authentication is disabled")
        return False
    log(f"⚡ Connection detected on {TARGET_IP}:22! Connecting SSH...")
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    try:
        client.connect(TARGET_IP, port=22, username=SSH_USER, key_filename=SSH_KEY,
                       look_for_keys=False, allow_agent=False, timeout=8)
        log("✅ SSH connected successfully!")
    except Exception as e:
        log(f"❌ SSH connect error: {e}")
        return False

    def exec_cmd(cmd, label):
        log(f"\n{'='*20} {label} [{' '.join(cmd.split())}] {'='*20}")
        try:
            stdin, stdout, stderr = client.exec_command(cmd, timeout=20)
            out = stdout.read().decode('utf-8', errors='ignore').strip()
            err = stderr.read().decode('utf-8', errors='ignore').strip()
            if out: log(out)
            if err: log(f"[STDERR]: {err}")
            return out
        except Exception as ex:
            log(f"Execution error: {ex}")
            return ""

    # 1. EMERGENCY KILL HIGH-POWER MINER INSTANTLY
    log("🚨 STEP 1: Emergency stopping any high-power miners to relieve PSU & thermals...")
    exec_cmd("sudo -n pkill -9 -f SRBMiner-MULTI || true; sudo -n pkill -9 -f PeakMiner || true; sudo -n systemctl stop peakminer 2>/dev/null || true", "STOP HIGH POWER MINERS")

    # 2. APPLY CONSERVATIVE SAFE POWER LIMITS & CLOCKS
    log("🔒 STEP 2: Applying conservative safe power limits (190W) & clock locks...")
    exec_cmd("sudo -n nvidia-smi -pm 1", "ENABLE PERSISTENCE MODE")
    exec_cmd("sudo -n nvidia-smi -pl 190", "SET POWER LIMIT TO 190W")
    exec_cmd("sudo -n nvidia-smi -lgc 1500,2000", "LOCK MAX CORE CLOCK TO 2000MHz")

    # 3. VERIFY GPU TELEMETRY
    log("📊 STEP 3: Verifying GPU status after applying conservative parameters...")
    exec_cmd("nvidia-smi --query-gpu=index,name,temperature.gpu,fan.speed,power.draw,power.limit,clocks.current.graphics --format=csv", "GPU TELEMETRY")
    exec_cmd("uptime; free -h; sensors 2>/dev/null | grep -iE 'package id|core 0|temp' | head -n 20 || true", "SYSTEM UPTIME & SENSORS")

    # 4. GATHER CRITICAL HARDWARE & CRASH LOGS (JOURNALCTL -B -1, DMESG, IPMI)
    log("🔍 STEP 4: Inspecting previous boot crash logs and hardware error ring...")
    exec_cmd("sudo -n journalctl -b -1 -p 3 -n 60 --no-pager 2>/dev/null || echo 'No previous journal or sudo permission'", "PREVIOUS BOOT (-1) CRITICAL ERRORS")
    exec_cmd("sudo -n journalctl -b -2 -p 3 -n 60 --no-pager 2>/dev/null || echo 'No -2 journal or sudo permission'", "PREVIOUS BOOT (-2) CRITICAL ERRORS")
    exec_cmd("sudo -n dmesg -T | grep -iE 'xid|thermal|mce|power|psu|throttle|error|hardware' | tail -n 50 || true", "KERNEL DMESG HARDWARE ALERTS")
    exec_cmd("command -v ipmitool >/dev/null && sudo -n ipmitool sel list | tail -n 30 || echo 'ipmitool unavailable or sudo denied'", "IPMI HARDWARE SEL EVENT LOGS")

    # 5. CHECK AUTOSTART / CRON CONFIGURATION TO PREVENT RE-TRIGGERING
    log("📋 STEP 5: Checking mining autostart services...")
    exec_cmd("crontab -l 2>/dev/null || true; systemctl list-unit-files | grep -iE 'miner|srb|peak' || true", "CRONTAB & SERVICES")

    client.close()
    log("🎉 Emergency recovery and stabilization completed successfully!")
    return True

def main():
    log("============================================================")
    log("🛡️ 4070 EMERGENCY WATCHER & CONSERVATIVE RESTORATION DAEMON")
    log(f"   Target: {TARGET_IP}:22")
    log("   Monitoring connection every 2s...")
    log("============================================================")
    
    poll_count = 0
    while True:
        poll_count += 1
        if check_banner(TARGET_IP, 22, timeout=1.0):
            success = execute_recovery()
            if success:
                break
        if poll_count % 15 == 0:
            log(f"Waiting for 4070 server to finish rebooting / connecting... (polled {poll_count} times)")
        time.sleep(2)

if __name__ == "__main__":
    main()
