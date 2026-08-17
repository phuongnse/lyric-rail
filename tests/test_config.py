from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import patch

from lyricrail.config import load_dotenv, resolve_data_root, resolve_project_root


class ConfigTests(unittest.TestCase):
    def test_explicit_root_has_priority(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(resolve_project_root(root), root.resolve())

    def test_environment_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"LYRICRAIL_HOME": directory}):
                self.assertEqual(resolve_project_root(), Path(directory).resolve())

    def test_mutable_data_root_is_independent_from_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as runtime_directory, tempfile.TemporaryDirectory() as data_directory:
            with patch.dict(os.environ, {"LYRICRAIL_DATA_HOME": data_directory}):
                self.assertEqual(
                    resolve_data_root(Path(runtime_directory)),
                    Path(data_directory).resolve(),
                )

    def test_relative_data_root_is_rejected(self) -> None:
        with patch.dict(os.environ, {"LYRICRAIL_DATA_HOME": "relative/data"}):
            with self.assertRaisesRegex(ValueError, "absolute"):
                resolve_data_root(Path.cwd())

    def test_dotenv_loads_quotes_without_overriding_existing_environment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            dotenv = Path(directory) / ".env"
            dotenv.write_text(
                "LYRICRAIL_TEST_ONE='hello world'\nLYRICRAIL_TEST_TWO=new\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"LYRICRAIL_TEST_TWO": "existing"}, clear=False):
                loaded = load_dotenv(dotenv)
                self.assertEqual(os.environ["LYRICRAIL_TEST_ONE"], "hello world")
                self.assertEqual(os.environ["LYRICRAIL_TEST_TWO"], "existing")
                self.assertEqual(loaded, 1)
            os.environ.pop("LYRICRAIL_TEST_ONE", None)

    def test_invalid_dotenv_line_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            dotenv = Path(directory) / ".env"
            dotenv.write_text("NOT_AN_ASSIGNMENT\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, ".env"):
                load_dotenv(dotenv)


if __name__ == "__main__":
    unittest.main()
