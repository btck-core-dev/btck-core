#!/usr/bin/env python3
import time
import json
import urllib.request
import urllib.parse
import http.cookiejar
import subprocess
import os

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_FILE = os.path.join(REPO_ROOT, "logs", "auto_onboard.log")
MINER_PASSWORD = os.environ.get("BTCK_MINER_PASSWORD")

MINERS = [
    {
        "name": "ks004",
        "ip": "192.168.50.6",
        "mode": "PPLNS",
        "wallet": "btck1s02095f1da0fbd3ec34f889d88454e5b5106f0d4eba0c05910499d8898211375af3035a2c5fc604bc35db78ba8b86babb6c5a80283819cc8451e9769e18f87e01aca2f10bce6d"
    },
    {
        "name": "ks003",
        "ip": "192.168.50.3",
        "mode": "SOLO",
        "wallet": "btck1s03df2cba1d3d4c84af0653034927852a5e83fc7d6a0373950f0b5b22c16a0961e203bb664f11e97fd8a1576aafd7f679bd12449b78b5d615c6c09de7f32cd07a98bd5d82ea60"
    },
    {
        "name": "ks002",
        "ip": "192.168.50.104",
        "mode": "SOLO",
        "wallet": "btck1s037d18eab628a24a59c21f2e92582b97f215f653a8c1617c16dfb156eb290d580a03125c2e3fb43623758f031a71c8f0d694943961e3827a6a0a838a25eee09323b0e467453d"
    }
]

POOL_URL = "stratum+tcp://192.168.50.189:3333"

def log(msg):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")

def try_onboard(miner):
    if not MINER_PASSWORD:
        raise RuntimeError("BTCK_MINER_PASSWORD is required")
    name = miner["name"]
    ip = miner["ip"]
    wallet = miner["wallet"]
    worker = f"{wallet}.{name}"
    
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    
    # 1. Login
    login_data = urllib.parse.urlencode({"post": 6, "user": "admin", "pwd": MINER_PASSWORD}).encode()
    req_login = urllib.request.Request(f"http://{ip}/user/loginpost", data=login_data, method="POST")
    try:
        with opener.open(req_login, timeout=2.0) as r:
            res_login = json.loads(r.read().decode())
            if res_login.get("error", -1) != 0:
                return False
    except Exception:
        return False
        
    # 2. Check panel
    panel_data = urllib.parse.urlencode({"post": 4}).encode()
    req_panel = urllib.request.Request(f"http://{ip}/user/userpanel", data=panel_data, method="POST")
    try:
        with opener.open(req_panel, timeout=2.0) as r:
            res_panel = json.loads(r.read().decode())
            if res_panel.get("error", -1) != 0:
                return False
            data = res_panel.get("data", {})
            fans = data.get("fans", [])
            pools = data.get("pools", [])
            # If already configured and fans > 5000, do nothing
            if fans and fans[0] >= 5200 and pools and pools[0].get("addr") == POOL_URL and pools[0].get("user") == worker:
                return True
    except Exception:
        return False

    # 3. Lock 100% fans
    fan_data = urllib.parse.urlencode({
        "post": 3,
        "fancontrol": "on",
        "fanratio": 100,
        "fanmode": "normal"
    }).encode()
    try:
        with opener.open(urllib.request.Request(f"http://{ip}/user/machineconfig", data=fan_data, method="POST"), timeout=2.0) as r:
            pass
        log(f"❄️ [{name} @ {ip}] Fan 100% full speed locked (5,900 RPM)!")
    except Exception as e:
        log(f"[{name} @ {ip}] Fan lock error: {e}")

    # 4. Set Stratum Pool
    pool_data = urllib.parse.urlencode({
        "post": 2,
        "pool1address": POOL_URL,
        "pool1miner": worker,
        "pool1pwd": "x",
        "pool2address": POOL_URL,
        "pool2miner": worker,
        "pool2pwd": "x",
        "pool3address": "",
        "pool3miner": "",
        "pool3pwd": ""
    }).encode()
    try:
        with opener.open(urllib.request.Request(f"http://{ip}/user/machineconfig", data=pool_data, method="POST"), timeout=2.5) as r:
            pass
        log(f"🎉 [{name} @ {ip}] POOL CONFIGURED -> {POOL_URL} with worker {name}!")
        pass
        return True
    except Exception as e:
        log(f"[{name} @ {ip}] Pool config error: {e}")
        return False

log("🚀 Auto-Onboard Reset Watcher started...")
while True:
    for m in MINERS:
        try_onboard(m)
    time.sleep(2.5)
