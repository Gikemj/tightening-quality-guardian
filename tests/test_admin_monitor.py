from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from torque_guard.admin_api import AdminService


class _ProjectDataHandler(BaseHTTPRequestHandler):
    requests = 0

    def log_message(self, format, *args):
        return

    def do_GET(self):
        if self.headers.get("Authorization") != "Bearer secret-test-key":
            self.send_response(401)
            self.end_headers()
            return
        type(self).requests += 1
        payload = json.dumps({"project": "demo", "records": 42}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class AdminMonitorTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.service = AdminService(Path(self.temp.name))
        _ProjectDataHandler.requests = 0
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _ProjectDataHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/project"

    def tearDown(self):
        self.service.stop_monitor()
        self.server.shutdown()
        self.server.server_close()
        self.temp.cleanup()

    def test_key_is_used_but_never_written_to_config(self):
        status = self.service.configure_monitor(
            {
                "name": "test source",
                "apiUrl": self.url,
                "authType": "bearer",
                "apiKey": "secret-test-key",
                "intervalSeconds": 2,
                "timeoutSeconds": 2,
            }
        )
        self.assertTrue(status["hasKey"])
        result = self.service.test_monitor()
        self.assertEqual(result["sample"]["data"]["records"], 42)
        persisted = self.service.monitor_config_path.read_text(encoding="utf-8")
        self.assertNotIn("secret-test-key", persisted)

    def test_background_monitor_starts_fetches_and_stops(self):
        self.service.configure_monitor(
            {
                "name": "test source",
                "apiUrl": self.url,
                "authType": "bearer",
                "apiKey": "secret-test-key",
                "intervalSeconds": 2,
                "timeoutSeconds": 2,
            }
        )
        self.assertTrue(self.service.start_monitor()["running"])
        deadline = time.time() + 2
        while time.time() < deadline and not self.service.monitor_status()["lastSample"]:
            time.sleep(0.02)
        self.assertIsNotNone(self.service.monitor_status()["lastSample"])
        self.assertFalse(self.service.stop_monitor()["running"])

    def test_key_and_url_validation_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "API Key"):
            self.service.configure_monitor(
                {"apiUrl": self.url, "authType": "bearer", "apiKey": ""}
            )
        with self.assertRaisesRegex(ValueError, "http"):
            self.service.configure_monitor(
                {"apiUrl": "file:///tmp/data.json", "authType": "none"}
            )


if __name__ == "__main__":
    unittest.main()
