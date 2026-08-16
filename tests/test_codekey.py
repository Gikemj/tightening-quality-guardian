import unittest

from torque_guard.integrations.codekey import CodeKeyTerraClient, CodeKeyTerraConfig, CodeKeyResponseError


class CodeKeyTerraClientTest(unittest.TestCase):
    def _dossier(self):
        return {
            "case": {"case_id": "CASE-DEMO-001", "equipment_family": "测试设备"},
            "facts": [{"evidence_id": "E-CASE-01", "label": "工单", "strength": "direct", "detail": "测试"}],
            "gaps": [{"evidence_id": "G-CASE-01", "label": "缺失", "strength": "gap", "detail": "测试"}],
            "tasks": [{"task_id": "TASK-REL-01"}],
        }

    def test_client_sends_bounded_request_and_accepts_safe_json(self):
        seen = {}

        def transport(url, headers, payload, timeout):
            seen.update({"url": url, "headers": headers, "payload": payload, "timeout": timeout})
            return {
                "choices": [{"message": {"content": '{"summary":"关系待核验。","review_questions":["请补充检查依据。"],"task_notes":[{"task_id":"TASK-REL-01","note":"核验关联后回写依据。"}],"safety":{"root_cause_confirmed":false,"automatic_action_allowed":false,"human_approval_required":true}}'}}]
            }

        client = CodeKeyTerraClient(
            CodeKeyTerraConfig(api_key="test-secret"), transport=transport
        )
        result = client.draft(self._dossier())

        self.assertEqual(seen["url"], "https://hetune.top/v1/chat/completions")
        self.assertEqual(seen["payload"]["model"], "gpt-5.6-sol")
        self.assertEqual(seen["payload"]["max_tokens"], 600)
        self.assertNotIn("test-secret", str(result))
        self.assertTrue(result["safety"]["human_approval_required"])

    def test_client_rejects_unsafe_model_claim(self):
        def transport(*_args):
            return {
                "choices": [{"message": {"content": '{"summary":"已确认根因。","review_questions":[],"task_notes":[],"safety":{"root_cause_confirmed":false,"automatic_action_allowed":false,"human_approval_required":true}}'}}]
            }

        client = CodeKeyTerraClient(
            CodeKeyTerraConfig(api_key="test-secret"), transport=transport
        )
        with self.assertRaises(CodeKeyResponseError):
            client.draft(self._dossier())

    def test_base_url_cannot_be_repointed(self):
        with self.assertRaises(ValueError):
            CodeKeyTerraConfig(api_key="x", base_url="https://example.com").validate()

    def test_base_url_rejects_non_exact_allowed_hosts(self):
        for base_url in ("https://hetune.top.evil.example", "https://hetune.top/path?x=1", "http://hetune.top"):
            with self.subTest(base_url=base_url), self.assertRaises(ValueError):
                CodeKeyTerraConfig(api_key="x", base_url=base_url).validate()

    def test_empty_optional_environment_uses_guarded_defaults(self):
        config = CodeKeyTerraConfig.from_env({"CODEKEY_API_KEY": "test-secret", "CODEKEY_BASE_URL": "", "CODEKEY_TERRA_MODEL": ""})
        self.assertEqual(config.base_url, "https://hetune.top")
        self.assertEqual(config.model, "gpt-5.6-sol")


if __name__ == "__main__":
    unittest.main()
