import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from audit_repository import require_deploy_resources, resolve_local_reference  # noqa: E402
from torque_guard import artifacts as artifact_io  # noqa: E402


class CanonicalArtifactTest(unittest.TestCase):
    def test_json_is_utf8_sorted_lf_terminated_and_round_trips(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "payload.json"
            artifact_io.write_json(target, {"锚点": 2, "alpha": {"z": 1, "a": 0}})
            raw = target.read_bytes()

        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))
        self.assertNotIn(b"\r", raw)
        self.assertTrue(raw.endswith(b"\n"))
        self.assertLess(raw.index(b'"alpha"'), raw.index('"锚点"'.encode("utf-8")))
        self.assertEqual(json.loads(raw.decode("utf-8"))["锚点"], 2)

    def test_invalid_non_finite_json_does_not_replace_existing_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "payload.json"
            artifact_io.write_json(target, {"valid": True})
            before = target.read_bytes()
            with self.assertRaises(ValueError):
                artifact_io.write_json(target, {"invalid": float("nan")})
            self.assertEqual(target.read_bytes(), before)

    def test_batch_commit_rolls_back_a_partial_replacement(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            staged = root / "staged"
            destination = root / "destination"
            staged.mkdir()
            destination.mkdir()
            (staged / "a.txt").write_bytes(b"new-a")
            (staged / "b.txt").write_bytes(b"new-b")
            (destination / "a.txt").write_bytes(b"old-a")
            (destination / "b.txt").write_bytes(b"old-b")

            real_writer = artifact_io.atomic_write_bytes

            def fail_second(path, payload):
                if Path(path).name == "b.txt" and payload == b"new-b":
                    raise OSError("simulated commit failure")
                real_writer(Path(path), payload)

            with mock.patch.object(artifact_io, "atomic_write_bytes", side_effect=fail_second):
                with self.assertRaisesRegex(OSError, "simulated commit failure"):
                    artifact_io.commit_staged_files(
                        staged, destination, [Path("a.txt"), Path("b.txt")]
                    )

            self.assertEqual((destination / "a.txt").read_bytes(), b"old-a")
            self.assertEqual((destination / "b.txt").read_bytes(), b"old-b")


class PublicPathResolutionTest(unittest.TestCase):
    def test_query_and_percent_encoding_resolve_inside_deploy_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            docs = Path(temporary) / "docs"
            docs.mkdir()
            index = docs / "index.html"
            asset = docs / "app.js"
            index.write_text("", encoding="utf-8")
            asset.write_text("", encoding="utf-8")

            self.assertEqual(
                resolve_local_reference(index, "./%61pp.js?v=7#ready", docs),
                asset.resolve(),
            )
            self.assertEqual(
                resolve_local_reference(index, "/app.js?cache=1", docs),
                asset.resolve(),
            )
            self.assertIsNone(
                resolve_local_reference(index, "https://example.com/app.js", docs)
            )

    def test_plain_encoded_and_double_encoded_traversal_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            docs = Path(temporary) / "docs"
            docs.mkdir()
            index = docs / "index.html"
            index.write_text("", encoding="utf-8")

            for reference in (
                "../secret.txt",
                "%2e%2e%2fsecret.txt",
                "%252e%252e%252fsecret.txt",
                "..%5csecret.txt?download=1",
                "%2f%2fexample.com%2fasset.js",
                "\\\\example.com\\asset.js",
            ):
                with self.subTest(reference=reference):
                    with self.assertRaisesRegex(ValueError, "escapes"):
                        resolve_local_reference(index, reference, docs)

    def test_nested_javascript_resources_are_checked_against_deploy_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            docs = root / "docs"
            docs.mkdir()
            index = docs / "index.html"
            app = docs / "app.js"
            nested = docs / "nested.js"
            index.write_text("", encoding="utf-8")
            app.write_text('import "./nested.js?v=1";\n', encoding="utf-8")
            nested.write_text("", encoding="utf-8")

            require_deploy_resources(index, ["./app.js"], docs)
            nested.write_text(
                'fetch("%252e%252e%252fsecret.json?download=1");\n', encoding="utf-8"
            )
            with self.assertRaisesRegex(AssertionError, "unsafe deployed"):
                require_deploy_resources(index, ["./app.js"], docs)


if __name__ == "__main__":
    unittest.main()
