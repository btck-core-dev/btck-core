#!/usr/bin/env python3
"""
watch_and_diagnose_4070.py
==========================
Continuously polls 4070 server over Tailscale (100.73.134.73).
The moment Tailscale/SSH becomes reachable:
1. Logs in with a configured SSH key and verified known-host entry
2. Captures real-time GPU thermals, VRAM junction temp, fan speed (nvidia-smi)
3. Captures CPU temperatures (sensors / sysfs thermal zones)
4. Checks previous boot crash logs (journalctl -b -1, dmesg) to identify why it buzzed/crashed
5. Inspects active processes to check if mining auto-started
6. Logs everything to logs/4070_hardware_diagnosis.log
"""

import time
import socket
import datetime
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_FILE = os.path.join(REPO_ROOT, "logs", "4070_hardware_diagnosis.log")
TARGET_IP = "100.73.134.73"
SSH_USER = "jczhang"
SSH_KEY = os.environ.get("BTCK_4070_SSH_KEY")

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

def check_ssh_banner(ip, port=22, timeout=1.0):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((ip, port))
        banner = s.recv(100)
        return banner.startswith(b"SSH-")
    except Exception:
        return False
    finally:
        s.close()

def diagnose_server(active_ip):
    import paramiko
    if not SSH_KEY:
        log("❌ BTCK_4070_SSH_KEY is required; password authentication is disabled")
        return False
    log(f"🔌 Valid SSH banner received on {active_ip}! Initiating SSH login...")
    
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    
    try:
        client.connect(active_ip, port=22, username=SSH_USER, key_filename=SSH_KEY,
                       look_for_keys=False, allow_agent=False, timeout=8)
        log(f"✅ Successfully authenticated to 4070 server via SSH ({active_ip})!")
    except Exception as e:
        log(f"❌ SSH Connection failed: {e}")
        return False

    def run(cmd, label):
        log(f"\n{'='*25} {label} [{' '.join(cmd.split())}] {'='*25}")
        try:
            stdin, stdout, stderr = client.exec_command(cmd, timeout=15)
            out = stdout.read().decode('utf-8', errors='ignore').strip()
            err = stderr.read().decode('utf-8', errors='ignore').strip()
            if out:
                log(out)
            if err:
                log(f"[STDERR]: {err}")
            return out
        except Exception as ex:
            log(f"Command execution error: {ex}")
            return ""

    # 1. Host identity & Uptime
    run("uptime; whoami; hostname; uname -r", "SYSTEM UPTIME & HOST")

    # 2. GPU Telemetry (Core Temp, VRAM Junction Temp, Fan Speed, Power, Throttle reasons)
    run("nvidia-smi --query-gpu=timestamp,name,driver_version,temperature.gpu,temperature.memory,fan.speed,power.draw,power.limit,clocks.current.graphics,clocks.current.memory,pstate --format=csv", "NVIDIA GPU CORE & VRAM TELEMETRY")
    run("nvidia-smi", "STANDARD NVIDIA-SMI OUTPUT")

    # 3. CPU & Motherboard Temperatures
    run("sensors 2>/dev/null || cat /sys/class/thermal/thermal_zone*/temp 2>/dev/null", "CPU & MOTHERBOARD SENSORS")

    # 4. Thermal & Hardware Error in Kernel Ring (dmesg)
    run("dmesg -T | grep -iE 'nvidia|thermal|throttle|mce|error|fail|oops|overheat|xid' | tail -n 40", "KERNEL HARDWARE & NVIDIA XID ERRORS")

    # 5. Check crash logs of previous boot
    run("journalctl -b -1 -p 3 -n 40 --no-pager 2>/dev/null || echo 'Previous boot journal not available'", "PREVIOUS BOOT CRITICAL LOGS")

    # 6. Check if Pearl miner or other GPU miners are running or enabled in systemd
    run("ps aux | grep -iE 'pearl|prl|miner|openclaw|tari|python' | grep -v grep", "ACTIVE PROCESSES (MINING / BACKGROUND)")

    # 7. Check RAM and Filesystem
    run("free -h; df -h /", "MEMORY & DISK USAGE")

    client.close()
    log("\n🎉 Full 4070 hardware diagnosis completed successfully.")
    return True

def main():
    log("=" * 60)
    log("🚀 4070 SERVER WATCHER & DIAGNOSIS DAEMON STARTED")
    log(f"   Target: {TARGET_IP}:22 (Tailscale)")
    log(f"   Waiting for 4070 server to power on and Tailscale to connect...")
    log("=" * 60)

    poll_count = 0
    while True:
        poll_count += 1
        if check_ssh_banner(TARGET_IP, 22, timeout=1.0):
            success = diagnose_server(TARGET_IP)
            if success:
                log("Watcher finished initial diagnosis. Switching to monitoring mode (sampling every 60s)...")
                while True:
                    time.sleep(60)
                    try:
                        import paramiko
                        c = paramiko.SSHClient()
                        c.load_system_host_keys()
                        c.set_missing_host_key_policy(paramiko.RejectPolicy())
                        c.connect(TARGET_IP, port=22, username=SSH_USER, key_filename=SSH_KEY,
                                  look_for_keys=False, allow_agent=False, timeout=5)
                        stdin, stdout, _ = c.exec_command("nvidia-smi --query-gpu=temperature.gpu,fan.speed,power.draw --format=csv,noheader,nounits; sensors 2>/dev/null | grep -i 'package id 0' || true")
                        metrics = stdout.read().decode('utf-8', errors='ignore').strip()
                        c.close()
                        log(f"📊 [4070 Periodic Telemetry] GPU/Fan/Power: {metrics}")
                    except Exception as e:
                        log(f"Periodic query error: {e}")
        
        if poll_count % 30 == 0:
            log(f"Waiting for 4070 server to boot & connect... (polled {poll_count} times)")
        time.sleep(2)

if __name__ == "__main__":
    main()
