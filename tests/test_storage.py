import json
import unittest
from tempfile import TemporaryDirectory
from pathlib import Path

from storage import load_last_history, save_message


class StorageTests(unittest.TestCase):
    def test_save_and_load_history(self):
        with TemporaryDirectory() as directory:
            path = Path(directory)
            save_message("42", "user", "Привет", path)
            save_message("42", "assistant", "Здравствуйте", path)

            history = load_last_history(path)

        self.assertEqual(
            history,
            {
                "42": [
                    {"role": "user", "content": "Привет"},
                    {"role": "assistant", "content": "Здравствуйте"},
                ]
            },
        )

    def test_invalid_latest_history_returns_empty_dict(self):
        with TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "history_2026-01-01.json").write_text("not json", encoding="utf-8")
            self.assertEqual(load_last_history(path), {})

    def test_history_with_wrong_top_level_type_returns_empty_dict(self):
        with TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "history_2026-01-01.json").write_text("[]", encoding="utf-8")
            self.assertEqual(load_last_history(path), {})

    def test_load_history_removes_unsupported_message_fields(self):
        with TemporaryDirectory() as directory:
            path = Path(directory)
            data = {"42": [{"role": "user", "content": "Привет", "time": "12:00:00"}]}
            (path / "history_2026-01-01.json").write_text(
                json.dumps(data, ensure_ascii=False), encoding="utf-8"
            )
            self.assertEqual(
                load_last_history(path),
                {"42": [{"role": "user", "content": "Привет"}]},
            )


if __name__ == "__main__":
    unittest.main()
