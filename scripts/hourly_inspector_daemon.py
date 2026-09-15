#!/usr/bin/env python3
import time
import subprocess
import os

inspector_path = "/Users/a1234/btck-core/scripts/hourly_mining_inspector.py"
while True:
    try:
        subprocess.run(["python3", inspector_path], check=False)
    except Exception as e:
        print(f"Inspector error: {e}")
    # Sleep 30 minutes
    time.sleep(1800)
