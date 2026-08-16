from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from torque_guard.admin_api import AdminService, ROOT


class AdminAiTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        shutil.copytree(ROOT / "knowledge", root / "knowledge")
        self.service = AdminService(root)
        self.service.generate_simulator_data({"scenario": "hidden_torque_drift", "strength": 1, "intervalSeconds": 2})

    def tearDown(self):
        self.service.stop_simulator()
        self.temp.cleanup()

    def test_local_fallback_uses_current_batch_context(self):
        result = self.service.ai_chat({"question": "为什么这批风险升高？"})
        self.assertEqual(result["source"], "local_rule_fallback")
        self.assertEqual(result["sequence"], 1)
        self.assertIn("触发原因", result["answer"])
        self.assertIn("评估总结", result["answer"])


if __name__ == "__main__":
    unittest.main()
