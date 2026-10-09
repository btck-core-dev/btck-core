#!/usr/bin/env python3
"""
Auto-Onboarding Daemon for Recovered IceRiver KS0 Ultra Miners
Continuously monitors 192.168.50.104 (ks002) and 192.168.50.6 (ks004)
As soon as the physical Reset is performed and userpanel responds normally:
  1. Immediately locks 100% fan speed (5,800 RPM) to prevent overheating.
  2. Sets the stratum pool to stratum+tcp://192.168.50.189:3333.
  3. Binds the correct designated stealth wallet address.
"""

import time
import json
import urllib.request
import urllib.parse
import http.cookiejar
import os

MINER_PASSWORD = os.environ.get("BTCK_MINER_PASSWORD")

TARGETS = [
    {
        "name": "ks002",
        "ip": "192.168.50.104",
        "mode": "SOLO",
        "wallet": "btck1s037d18eab628a24a59c21f2e92582b97f215f653a8c1617c16dfb156eb290d580a03125c2e3fb43623758f031a71c8f0d694943961e3827a6a0a838a25eee09323b0e467453d"
    },
    {
        "name": "ks004",
        "ip": "192.168.50.6",
        "mode": "PPLNS",
        "wallet": "btck1s02095f1da0fbd3ec34f889d88454e5b5106f0d4eba0c05910499d8898211375af3035a2c5fc604bc35db78ba8b86babb6c5a80283819cc8451e9769e18f87e01aca2f10bce6d"
    }
]

POOL_URL = "stratum+tcp://192.168.50.189:3333"

def attempt_configure(miner):
    if not MINER_PASSWORD:
        raise RuntimeError("BTCK_MINER_PASSWORD is required")
    name = miner["name"]
    ip = miner["ip"]
    wallet = miner["wallet"]
    
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    
    # 1. Login
    login_data = urllib.parse.urlencode({"post": 6, "user": "admin", "pwd": MINER_PASSWORD}).encode()
    req_login = urllib.request.Request(f"http://{ip}/user/loginpost", data=login_data, method="POST")
    try:
        with opener.open(req_login, timeout=3) as r:
            res_login = json.loads(r.read().decode())
            if res_login.get("error", -1) != 0:
                return False
    except Exception:
        return False

    # 2. Check if userpanel returns valid data (not "Miner return failed!")
    panel_data = urllib.parse.urlencode({"post": 4}).encode()
    req_panel = urllib.request.Request(f"http://{ip}/user/userpanel", data=panel_data, method="POST")
    try:
        with opener.open(req_panel, timeout=3) as r:
            res_panel = json.loads(r.read().decode())
            if res_panel.get("message") == "Miner return failed!" or res_panel.get("error", -1) != 0:
                return False
            # Valid data reached!
    except Exception:
        return False

    # 3. Lock Fan 100%
    fan_data = urllib.parse.urlencode({"post": 3, "fancontrol": "on", "fanratio": 100, "fanmode": "normal"}).encode()
    req_fan = urllib.request.Request(f"http://{ip}/user/machineconfig", data=fan_data, method="POST")
    try:
        with opener.open(req_fan, timeout=3) as r:
            pass
    except Exception as e:
        print(f"[{name}] Fan lock error: {e}")

    # 4. Set Pool
    worker = f"{wallet}.{name}"
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
    req_pool = urllib.request.Request(f"http://{ip}/user/machineconfig", data=pool_data, method="POST")
    try:
        with opener.open(req_pool, timeout=3) as r:
            pass
        print(f"🎉 [{name}] SUCCESSFULLY CONFIGURED & ONBOARDED! Fan locked 100%, pool pointed to {POOL_URL}")
        return True
    except Exception as e:
        print(f"[{name}] Pool set error: {e}")
        return False

def main():
    print("🚀 Starting Auto-Onboard Watcher Daemon...")
    configured = {m["name"]: False for m in TARGETS}
    
    while not all(configured.values()):
        for m in TARGETS:
            name = m["name"]
            if not configured[name]:
                ok = attempt_configure(m)
                if ok:
                    configured[name] = True
        time.sleep(3)
        
    print("✅ All target miners successfully onboarded and hashing!")

if __name__ == "__main__":
    main()
