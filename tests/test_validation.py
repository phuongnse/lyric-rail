from pathlib import Path
import unittest

from lyricrail.validation import validate_project


class ValidationTests(unittest.TestCase):
    def test_repository_configuration_is_valid(self) -> None:
        root = Path(__file__).resolve().parents[1]
        report = validate_project(root)
        self.assertTrue(report["valid"])
        self.assertEqual(report["summary"]["errors"], 0)


if __name__ == "__main__":
    unittest.main()
