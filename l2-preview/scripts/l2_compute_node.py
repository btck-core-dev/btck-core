#!/usr/bin/env python3
"""Start the single-entry resident GPU compute node."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from worker.compute_node import main
if __name__=='__main__':main()
