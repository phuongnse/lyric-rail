from pathlib import Path
import unittest

from lyricrail.toolchain import collect_doctor_report


class ToolchainTests(unittest.TestCase):
    def test_report_is_structured(self) -> None:
        root = Path(__file__).resolve().parents[1]
        report = collect_doctor_report(root)
        self.assertEqual(report["product"], "LyricRail")
        self.assertIn("platform", report)
        self.assertIn("checks", report)
        self.assertTrue(any(check["name"] == "python" for check in report["checks"]))


if __name__ == "__main__":
    unittest.main()
