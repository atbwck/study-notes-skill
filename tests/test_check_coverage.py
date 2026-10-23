import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest


SPEC = importlib.util.spec_from_file_location(
    "check_coverage", Path(__file__).resolve().parents[1] / "scripts" / "check_coverage.py"
)
coverage = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(coverage)


class CoverageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "note.md").write_text(
            "# Notes\n\n## Limits\n\nRetry only when safe.\n", encoding="utf-8"
        )
        self.data = {
            "scope": "Manual retry section",
            "sources": [{"id": "S1", "location": "manual.pdf", "access": "complete"}],
            "units": [{"id": "U1", "source": "S1", "locator": "p.2", "status": "read"}],
            "items": [{
                "id": "K1", "unit": "U1", "locator": "p.2 paragraph 3",
                "point": "Retry only if the request is safe to repeat.",
                "status": "covered", "target": {"file": "note.md", "heading": "Limits"}
            }],
        }

    def check(self):
        return coverage.validate(self.data, self.root)

    def test_covered_and_merged_points_resolve(self):
        other = copy.deepcopy(self.data["items"][0])
        other.update(id="K2", status="merged", locator="p.2 paragraph 4")
        self.data["items"].append(other)
        self.assertEqual(self.check(), ([], []))

    def test_declared_gap_is_incomplete(self):
        self.data["items"][0].update(status="gap", reason="Formula not legible.")
        errors, gaps = self.check()
        self.assertEqual(errors, [])
        self.assertTrue(gaps)

    def test_partial_source_cannot_pass(self):
        self.data["sources"][0].update(access="partial", reason="Appendix unavailable.")
        errors, gaps = self.check()
        self.assertEqual(errors, [])
        self.assertTrue(gaps)

    def test_unavailable_source_cannot_have_read_units(self):
        self.data["sources"][0].update(access="unavailable", reason="No access.")
        self.assertTrue(self.check()[0])

    def test_missing_file_and_heading_fail(self):
        for target in (
            {"file": "missing.md", "heading": "Limits"},
            {"file": "note.md", "heading": "Missing section"},
        ):
            with self.subTest(target=target):
                self.data["items"][0]["target"] = target
                self.assertTrue(self.check()[0])

    def test_duplicate_heading_is_ambiguous(self):
        (self.root / "note.md").write_text("## Limits\nA\n## Limits\nB\n", encoding="utf-8")
        self.assertTrue(self.check()[0])

    def test_heading_in_code_block_is_not_a_target(self):
        (self.root / "note.md").write_text("```md\n## Limits\n```\n", encoding="utf-8")
        self.assertTrue(self.check()[0])

    def test_empty_read_unit_fails(self):
        self.data["items"] = []
        self.assertTrue(self.check()[0])

    def test_unknown_units_and_duplicate_ids_fail(self):
        self.data["items"][0]["unit"] = "U-missing"
        self.data["items"].append(copy.deepcopy(self.data["items"][0]))
        self.assertTrue(self.check()[0])

    def test_omission_needs_allowed_reason_and_duplicate_target(self):
        item = self.data["items"][0]
        item.update(status="omitted", basis="shorten", reason="Keep notes short.")
        self.assertTrue(self.check()[0])
        item["basis"] = "duplicate"
        del item["target"]
        self.assertTrue(self.check()[0])
        item["basis"] = "out_of_scope"
        self.assertEqual(self.check(), ([], []))

    def test_malformed_inputs_return_errors(self):
        for data in (None, [], {}, {"sources": "S1"}, {"items": [42]}):
            with self.subTest(data=data):
                self.assertTrue(coverage.validate(data, self.root)[0])

    def test_cli_exit_codes_and_read_only_behavior(self):
        ledger = self.root / "audit.json"
        for status, expected in (("covered", 0), ("gap", 2), ("invalid", 1)):
            self.data["items"][0].update(status=status, reason="Waiting for source.")
            text = json.dumps(self.data)
            ledger.write_text(text, encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(coverage.main([str(ledger)]), expected)
            self.assertEqual(ledger.read_text(encoding="utf-8"), text)

    def test_cli_rejects_invalid_json(self):
        ledger = self.root / "audit.json"
        ledger.write_text("{", encoding="utf-8")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(coverage.main([str(ledger)]), 1)

    def test_nonhashable_statuses_are_reported(self):
        self.data["items"][0]["status"] = ["covered"]
        self.data["sources"][0]["access"] = {"complete": True}
        self.assertTrue(self.check()[0])


if __name__ == "__main__":
    unittest.main()
