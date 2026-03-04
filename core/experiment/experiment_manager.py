"""Experiment lifecycle manager.

Owns experiment start/stop logic including TCP command broadcasting and
latency scheduling. The TUI delegates to this class instead of managing
experiment state directly.

Dependencies: only TCPConnectionManager. No TUI knowledge.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

from core.tcp.connection_manager import TCPConnectionManager

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExperimentConfig:
    """Immutable configuration for a single experiment."""

    experiment_id: int
    latency_sequence: Optional[list[int]] = None
    interval_sec: float = 300.0  # 5 min between latency steps
    random_pick: bool = False  # randomly pick from sequence each step
    start_commands: list[str] = field(
        default_factory=lambda: ["OBS_ON_ALL", "INPUT_ON", "SERVER_ON"]
    )
    stop_commands: list[str] = field(
        default_factory=lambda: ["OBS_OFF_ALL", "INPUT_OFF", "STOP", "SERVER_OFF"]
    )


# Pre-defined experiment configs
EXP_1 = ExperimentConfig(experiment_id=1)  # baseline, no latency
EXP_2 = ExperimentConfig(
    experiment_id=2,
    latency_sequence=[0, 20, 40, 60, 80, 100],
    random_pick=True,
)
EXP_3 = ExperimentConfig(experiment_id=3, latency_sequence=[50, 0, 100, 200])
EXPERIMENT_CONFIGS: dict[int, ExperimentConfig] = {1: EXP_1, 2: EXP_2, 3: EXP_3}


class ExperimentManager:
    """Manages experiment lifecycle: start/stop, command broadcasting, latency scheduling.

    Thread-safe via internal lock on mutable state.
    """

    def __init__(self, tcp_connection_manager: TCPConnectionManager) -> None:
        self._tcp = tcp_connection_manager
        self._lock = threading.Lock()
        self._active_config: Optional[ExperimentConfig] = None
        self._schedule_stop: Optional[threading.Event] = None
        self._schedule_thread: Optional[threading.Thread] = None
        self._current_step_index: Optional[int] = None
        self._step_started_at: Optional[float] = None

    # ── Properties ────────────────────────────────────────────────

    @property
    def active_experiment(self) -> Optional[int]:
        """Which experiment is currently running, or None."""
        with self._lock:
            return self._active_config.experiment_id if self._active_config else None

    @property
    def is_active(self) -> bool:
        with self._lock:
            return self._active_config is not None

    @property
    def current_latency_step(self) -> Optional[tuple[int, int]]:
        """(step_index, latency_ms) of the active latency value, or None."""
        with self._lock:
            if (
                self._active_config is None
                or self._active_config.latency_sequence is None
                or self._current_step_index is None
            ):
                return None
            idx = self._current_step_index
            lat = self._active_config.latency_sequence[idx]
            return (idx, lat)

    @property
    def latency_step_count(self) -> Optional[int]:
        """Total number of steps in the active schedule, or None."""
        with self._lock:
            if (
                self._active_config is None
                or self._active_config.latency_sequence is None
            ):
                return None
            return len(self._active_config.latency_sequence)

    @property
    def seconds_until_next_step(self) -> Optional[float]:
        """Seconds remaining until the next latency change, or None."""
        with self._lock:
            if (
                self._active_config is None
                or self._active_config.latency_sequence is None
                or self._step_started_at is None
            ):
                return None
            elapsed = time.monotonic() - self._step_started_at
            remaining = self._active_config.interval_sec - elapsed
            return max(0.0, remaining)

    # ── Start / Stop ──────────────────────────────────────────────

    def start(self, exp_num: int) -> bool:
        """Start an experiment. Returns True on success, False if already active or unknown."""
        config = EXPERIMENT_CONFIGS.get(exp_num)
        if config is None:
            logger.warning(f"Unknown experiment number: {exp_num}")
            return False

        with self._lock:
            if self._active_config is not None:
                logger.warning(
                    f"Experiment {self._active_config.experiment_id} already active"
                )
                return False
            self._active_config = config
            self._current_step_index = None

        # Clear previous latency state before starting (reference pattern)
        try:
            self._tcp.broadcast_cmd("STOP")
        except Exception as e:
            logger.warning(f"Exp {exp_num}: failed to clear previous state: {e}")

        # Broadcast start commands
        for cmd in config.start_commands:
            logger.info(f"Exp {exp_num}: broadcasting {cmd}")
            self._tcp.broadcast_cmd(cmd)

        # Start latency schedule thread for experiments with a sequence
        if config.latency_sequence is not None:
            stop_event = threading.Event()
            thread = threading.Thread(
                target=self._run_latency_schedule,
                args=(config, stop_event),
                daemon=True,
                name=f"exp-{exp_num}-schedule",
            )
            with self._lock:
                self._schedule_stop = stop_event
                self._schedule_thread = thread
            thread.start()
            logger.info(
                f"Exp {exp_num}: Latency schedule started "
                f"({' -> '.join(str(v) for v in config.latency_sequence)} ms, "
                f"every {config.interval_sec / 60:.0f} min)"
            )
        else:
            logger.info(f"Exp {exp_num}: Baseline (no latency)")

        return True

    def stop(self) -> bool:
        """Stop the active experiment. Returns True if one was stopped."""
        with self._lock:
            if self._active_config is None:
                return False
            exp_num = self._active_config.experiment_id
            config = self._active_config
            stop_event = self._schedule_stop
            thread = self._schedule_thread

        # Signal and join schedule thread
        if stop_event is not None:
            stop_event.set()
        if thread is not None:
            thread.join(timeout=5.0)

        # Broadcast stop commands (always clear state even if broadcast fails)
        try:
            for cmd in config.stop_commands:
                logger.info(f"Exp {exp_num}: broadcasting {cmd}")
                self._tcp.broadcast_cmd(cmd)
        except Exception as e:
            logger.error(f"Exp {exp_num}: error broadcasting stop commands: {e}")
        finally:
            # Always clear state
            with self._lock:
                self._active_config = None
                self._schedule_stop = None
                self._schedule_thread = None
                self._current_step_index = None
                self._step_started_at = None

        logger.info(f"Experiment {exp_num} stopped")
        return True

    # ── Latency schedule (runs in daemon thread) ──────────────────

    def _run_latency_schedule(
        self, config: ExperimentConfig, stop_event: threading.Event
    ) -> None:
        """Loop through latency sequence, broadcasting SET_LATENCY at each step."""
        assert config.latency_sequence is not None
        seq = config.latency_sequence
        while not stop_event.is_set():
            if config.random_pick:
                order = list(range(len(seq)))
                random.shuffle(order)
            else:
                order = list(range(len(seq)))
            for idx in order:
                if stop_event.is_set():
                    break
                lat_ms = seq[idx]
                with self._lock:
                    self._current_step_index = idx
                    self._step_started_at = time.monotonic()
                results = self._tcp.broadcast_cmd(f"SET_LATENCY {lat_ms}")
                applied = sum(1 for r in results.values() if r is not None)
                logger.info(
                    f"Exp schedule: SET_LATENCY {lat_ms} "
                    f"applied to {applied}/{len(results)} clients"
                )
                if stop_event.wait(config.interval_sec):
                    break
