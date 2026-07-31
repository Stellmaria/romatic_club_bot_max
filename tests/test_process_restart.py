from __future__ import annotations

import asyncio
import unittest

from bot.core.process_restart import ProcessRestartCoordinator


class ProcessRestartCoordinatorTests(unittest.IsolatedAsyncioTestCase):
    async def test_duplicate_request_is_rejected_until_termination_runs(self) -> None:
        terminated = asyncio.Event()
        coordinator = ProcessRestartCoordinator(terminator=terminated.set)

        self.assertTrue(await coordinator.request(delay_seconds=0.1))
        self.assertTrue(coordinator.pending)
        self.assertFalse(await coordinator.request(delay_seconds=0.1))

        await asyncio.wait_for(terminated.wait(), timeout=1)
        await asyncio.sleep(0)
        self.assertFalse(coordinator.pending)

    async def test_new_request_is_allowed_after_previous_task_finishes(self) -> None:
        calls = 0

        def terminate() -> None:
            nonlocal calls
            calls += 1

        coordinator = ProcessRestartCoordinator(terminator=terminate)

        self.assertTrue(await coordinator.request(delay_seconds=0.1))
        await asyncio.sleep(0.15)
        self.assertEqual(calls, 1)

        self.assertTrue(await coordinator.request(delay_seconds=0.1))
        await asyncio.sleep(0.15)
        self.assertEqual(calls, 2)


if __name__ == "__main__":
    unittest.main()
