import unittest
from pathlib import Path

from torque_guard.knowledge import KnowledgeBase


ROOT = Path(__file__).resolve().parents[1]


class KnowledgeGraphTest(unittest.TestCase):
    def test_subgraph_connects_signal_to_verification_actions(self):
        bundle = KnowledgeBase(ROOT / "knowledge").retrieve("P03")
        node_ids = {node["id"] for node in bundle.subgraph["nodes"]}
        self.assertIn("FM-PRELOAD-UNSTABLE", node_ids)
        self.assertIn("C-SOCKET-WEAR", node_ids)
        self.assertIn("A-SOCKET-CHECK", node_ids)
        self.assertGreaterEqual(len(bundle.historical_cases), 2)


if __name__ == "__main__":
    unittest.main()
