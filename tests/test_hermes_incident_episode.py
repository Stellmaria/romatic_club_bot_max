from __future__ import annotations

import time

from scripts.hermes_incident_monitor import Monitor, Probe


def _monitor() -> Monitor:
    monitor = Monitor.__new__(Monitor)
    monitor._incident_episode_open = False
    monitor._active_status = "completed"
    monitor._last_event_key = ""
    monitor._last_event_at = 0.0
    monitor.cooldown_seconds = 600
    return monitor


def test_open_incident_episode_blocks_follow_up_state_changes() -> None:
    monitor = _monitor()
    monitor._incident_episode_open = True

    assert not monitor._can_submit("bot|container-unhealthy|abc|1|unhealthy")
    assert not monitor._can_submit("bot|container-not-running|missing|1|none")


def test_existing_cooldown_still_applies_without_open_episode() -> None:
    monitor = _monitor()
    monitor._last_event_key = "bot|container-not-running|missing|0|none"
    monitor._last_event_at = time.time()

    assert not monitor._can_submit(monitor._last_event_key)
    assert monitor._can_submit("userbot|container-not-running|missing|0|none")


def test_only_running_healthy_probe_counts_as_recovered() -> None:
    healthy = Probe("bot", "abc", True, "running", "healthy", 0, 0)
    starting = Probe("bot", "abc", True, "running", "starting", 0, 0)
    stopped = Probe("bot", "abc", False, "exited", None, 0, 1)

    assert Monitor._is_recovered(healthy)
    assert not Monitor._is_recovered(starting)
    assert not Monitor._is_recovered(stopped)
