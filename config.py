"""Environment-backed application settings."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def load_env_file(path: Path = Path(".env")) -> None:
    """Load simple KEY=VALUE entries without overwriting exported variables."""
    if not path.is_file():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env_file()


@dataclass(frozen=True)
class Settings:
    telegram_token: str
    openai_api_key: str
    guru_chat_id: int
    history_dir: Path
    cartesia_api_key: str | None = None
    cartesia_version: str | None = None
    cartesia_model_id: str | None = None
    cartesia_voice_id: str | None = None

    @property
    def cartesia_enabled(self) -> bool:
        return all(
            (
                self.cartesia_api_key,
                self.cartesia_version,
                self.cartesia_model_id,
                self.cartesia_voice_id,
            )
        )


def load_settings() -> Settings:
    """Load settings and fail early when required values are missing."""
    telegram_token = os.getenv("TELEGRAM_TOKEN")
    openai_api_key = os.getenv("OPENAI_API_KEY")

    missing = [
        name
        for name, value in (
            ("TELEGRAM_TOKEN", telegram_token),
            ("OPENAI_API_KEY", openai_api_key),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")

    guru_chat_id_value = os.getenv("GURU_CHAT_ID", "642590466")
    try:
        guru_chat_id = int(guru_chat_id_value)
    except ValueError as exc:
        raise RuntimeError("GURU_CHAT_ID must be an integer") from exc

    return Settings(
        telegram_token=telegram_token,
        openai_api_key=openai_api_key,
        guru_chat_id=guru_chat_id,
        history_dir=Path(os.getenv("HISTORY_DIR", ".")),
        cartesia_api_key=os.getenv("CARTESIA_API_KEY"),
        cartesia_version=os.getenv("CARTESIA_VERSION"),
        cartesia_model_id=os.getenv("CARTESIA_MODEL_ID"),
        cartesia_voice_id=os.getenv("CARTESIA_VOICE_ID"),
    )
