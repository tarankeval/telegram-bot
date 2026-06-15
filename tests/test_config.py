import os
import unittest
from unittest.mock import patch

from config import load_env_file, load_settings


class SettingsTests(unittest.TestCase):
    def test_required_settings_are_validated(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "TELEGRAM_TOKEN, OPENAI_API_KEY"):
                load_settings()

    def test_cartesia_requires_complete_configuration(self):
        environment = {
            "TELEGRAM_TOKEN": "telegram",
            "OPENAI_API_KEY": "openai",
            "CARTESIA_API_KEY": "cartesia",
        }
        with patch.dict(os.environ, environment, clear=True):
            self.assertFalse(load_settings().cartesia_enabled)

    def test_complete_settings(self):
        environment = {
            "TELEGRAM_TOKEN": "telegram",
            "OPENAI_API_KEY": "openai",
            "GURU_CHAT_ID": "123",
            "CARTESIA_API_KEY": "cartesia",
            "CARTESIA_VERSION": "version",
            "CARTESIA_MODEL_ID": "model",
            "CARTESIA_VOICE_ID": "voice",
        }
        with patch.dict(os.environ, environment, clear=True):
            settings = load_settings()

        self.assertEqual(settings.guru_chat_id, 123)
        self.assertEqual(str(settings.history_dir), ".")
        self.assertTrue(settings.cartesia_enabled)

    def test_env_file_does_not_override_exported_values(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path

        with TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text("TOKEN=from-file\nQUOTED='value'\n", encoding="utf-8")
            with patch.dict(os.environ, {"TOKEN": "exported"}, clear=True):
                load_env_file(env_file)
                self.assertEqual(os.environ["TOKEN"], "exported")
                self.assertEqual(os.environ["QUOTED"], "value")


if __name__ == "__main__":
    unittest.main()
