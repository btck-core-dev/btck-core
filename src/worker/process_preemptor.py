"""
BTC-K Process Preemptor Module
Controls sub-second GPU mining preemption using OS-level SIGSTOP and SIGCONT signals,
with automated watchdog recovery to prevent GPU freeze.
"""

import logging
import os
import signal
import subprocess
import threading
import time
from enum import Enum
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("BTCK.ProcessPreemptor")


class MinerState(str, Enum):
    UNMANAGED = "UNMANAGED"
    MINING_ACTIVE = "MINING_ACTIVE"
    PAUSED_FOR_AI = "PAUSED_FOR_AI"
    TERMINATED = "TERMINATED"
    ERROR = "ERROR"


class ProcessPreemptor:
    """
    Sub-second OS process preemptor for GPU miners (e.g. SRBMiner, pearld, etc.).
    """

    def __init__(
        self,
        pid: Optional[int] = None,
        max_preemption_timeout_sec: float = 30.0,
        enable_watchdog: bool = True,
    ):
        self.pid = pid
        self.max_timeout_sec = max_preemption_timeout_sec
        self.enable_watchdog = enable_watchdog
        self.state = MinerState.MINING_ACTIVE if pid else MinerState.UNMANAGED
        self._lock = threading.Lock()
        self._watchdog_timer: Optional[threading.Timer] = None
        self.stats = {
            "total_preemptions": 0,
            "total_paused_seconds": 0.0,
            "watchdog_auto_resumes": 0,
            "last_pause_duration_ms": 0.0,
            "last_resume_duration_ms": 0.0,
        }
        self._paused_timestamp: Optional[float] = None

    def attach_pid(self, pid: int) -> bool:
        """Attach to an existing running miner PID."""
        with self._lock:
            try:
                # Check if PID exists by sending signal 0
                os.kill(pid, 0)
                self.pid = pid
                self.state = MinerState.MINING_ACTIVE
                return True
            except OSError:
                logger.error(f"Cannot attach to non-existent PID: {pid}")
                self.state = MinerState.ERROR
                return False

    def pause(self) -> Tuple[bool, float]:
        """
        Freeze the mining process using SIGSTOP.
        Returns (success, elapsed_ms).
        """
        with self._lock:
            if not self.pid:
                return False, 0.0

            if self.state == MinerState.PAUSED_FOR_AI:
                return True, 0.0  # Already paused

            start_t = time.perf_counter()
            try:
                os.kill(self.pid, signal.SIGSTOP)
                elapsed_ms = (time.perf_counter() - start_t) * 1000.0
                self.state = MinerState.PAUSED_FOR_AI
                self._paused_timestamp = time.time()
                self.stats["total_preemptions"] += 1
                self.stats["last_pause_duration_ms"] = round(elapsed_ms, 2)

                # Arm safety watchdog to prevent permanent GPU hang
                if self.enable_watchdog:
                    self._arm_watchdog()

                logger.info(f"Miner PID {self.pid} paused in {elapsed_ms:.2f}ms (SIGSTOP)")
                return True, elapsed_ms
            except OSError as e:
                logger.error(f"Failed to pause PID {self.pid}: {e}")
                self.state = MinerState.ERROR
                return False, 0.0

    def resume(self) -> Tuple[bool, float]:
        """
        Unfreeze the mining process using SIGCONT.
        Returns (success, elapsed_ms).
        """
        with self._lock:
            return self._resume_internal()

    def _resume_internal(self) -> Tuple[bool, float]:
        """Internal resume implementation."""
        self._disarm_watchdog()

        if not self.pid:
            return False, 0.0

        if self.state == MinerState.MINING_ACTIVE:
            return True, 0.0  # Already active

        start_t = time.perf_counter()
        try:
            os.kill(self.pid, signal.SIGCONT)
            elapsed_ms = (time.perf_counter() - start_t) * 1000.0
            self.state = MinerState.MINING_ACTIVE

            if self._paused_timestamp:
                duration = time.time() - self._paused_timestamp
                self.stats["total_paused_seconds"] += duration
                self._paused_timestamp = None

            self.stats["last_resume_duration_ms"] = round(elapsed_ms, 2)
            logger.info(f"Miner PID {self.pid} resumed in {elapsed_ms:.2f}ms (SIGCONT)")
            return True, elapsed_ms
        except OSError as e:
            logger.error(f"Failed to resume PID {self.pid}: {e}")
            self.state = MinerState.ERROR
            return False, 0.0

    def _arm_watchdog(self):
        """Schedule an automatic resume if AI task hangs."""
        self._disarm_watchdog()
        self._watchdog_timer = threading.Timer(self.max_timeout_sec, self._on_watchdog_timeout)
        self._watchdog_timer.daemon = True
        self._watchdog_timer.start()

    def _disarm_watchdog(self):
        """Cancel watchdog timer."""
        if self._watchdog_timer:
            self._watchdog_timer.cancel()
            self._watchdog_timer = None

    def _on_watchdog_timeout(self):
        """Watchdog fired: AI task timed out, auto-resume mining."""
        logger.warning(
            f"Watchdog fired! Preemption timed out ({self.max_timeout_sec}s). Auto-resuming miner PID {self.pid}..."
        )
        with self._lock:
            if self.state == MinerState.PAUSED_FOR_AI:
                self.stats["watchdog_auto_resumes"] += 1
                self._resume_internal()

    def get_status(self) -> Dict:
        """Return preemptor diagnostics."""
        with self._lock:
            return {
                "pid": self.pid,
                "state": self.state.value,
                "stats": self.stats.copy(),
                "watchdog_active": self._watchdog_timer is not None,
                "timeout_sec": self.max_timeout_sec,
            }
