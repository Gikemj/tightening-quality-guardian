from __future__ import annotations

import shutil
import tempfile
import time
import unittest
from pathlib import Path

from torque_guard.admin_api import AdminService, ROOT


class AdminSimulatorTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        shutil.copytree(ROOT / "knowledge", root / "knowledge")
        self.service = AdminService(root)

    def tearDown(self):
        self.service.stop_simulator()
        self.temp.cleanup()

    def test_generate_uses_risk_analyzer_and_returns_synthetic_card(self):
        result = self.service.generate_simulator_data(
            {"scenario": "hidden_torque_drift", "strength": 1.25, "intervalSeconds": 2}
        )
        self.assertTrue(result["active"])
        self.assertTrue(result["synthetic"])
        self.assertEqual(result["sequence"], 1)
        self.assertEqual(result["card"]["analysis_provenance"]["generated_by"], "torque_guard.risk.RiskAnalyzer")
        self.assertEqual(len(result["latestEvents"]), 24)

    def test_continuous_simulator_can_start_and_stop(self):
        self.service.configure_simulator({"scenario": "normal", "strength": 1, "intervalSeconds": 2})
        self.assertTrue(self.service.start_simulator()["running"])
        deadline = time.time() + 2
        while time.time() < deadline and self.service.simulator_status()["sequence"] == 0:
            time.sleep(0.02)
        self.assertGreaterEqual(self.service.simulator_status()["sequence"], 1)
        self.assertFalse(self.service.stop_simulator()["running"])


if __name__ == "__main__":
    unittest.main()
