from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class HermesOperatorControlContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")

    def _service(self, name: str, end_marker: str) -> str:
        marker = f"  {name}:"
        return self.compose.split(marker, 1)[1].split(end_marker, 1)[0]

    def test_only_supervisor_proxy_joins_hermes_control_network(self) -> None:
        proxy = self._service("supervisor-proxy", "\n  bot:")
        bot = self._service("bot", "\n  userbot:")
        userbot = self._service("userbot", "\nvolumes:")

        self.assertIn("hermes-supervisor-control:", proxy)
        self.assertIn("romatic-supervisor", proxy)
        self.assertNotIn("hermes-supervisor-control", bot)
        self.assertNotIn("hermes-supervisor-control", userbot)

    def test_control_network_is_internal_and_attachable(self) -> None:
        network = self.compose.split("\nnetworks:\n", 1)[1]
        self.assertIn(
            "name: ${HERMES_SUPERVISOR_NETWORK:-hermes-supervisor-control}",
            network,
        )
        self.assertIn("driver: bridge", network)
        self.assertIn("internal: true", network)
        self.assertIn("attachable: true", network)
        self.assertNotIn("external: true", network)

    def test_proxy_keeps_existing_hardening_and_no_host_port(self) -> None:
        proxy = self._service("supervisor-proxy", "\n  bot:")
        self.assertIn("read_only: true", proxy)
        self.assertIn("cap_drop:\n      - ALL", proxy)
        self.assertIn("no-new-privileges:true", proxy)
        self.assertNotIn("ports:", proxy)
        self.assertNotIn("docker.sock", proxy)
        self.assertNotIn("/srv/romatic-club-max", proxy)


if __name__ == "__main__":
    unittest.main()
