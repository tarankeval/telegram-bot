"""Small JSON-backed conversation history store."""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path


_write_lock = threading.Lock()


def load_last_history(directory: Path = Path(".")) -> dict[str, list[dict[str, str]]]:
    files = sorted(directory.glob("history_*.json"))
    if not files:
        return {}

    try:
        with files[-1].open("r", encoding="utf-8") as file:
            raw_history = json.load(file)
            if not isinstance(raw_history, dict):
                return {}
            return {
                str(chat_id): [
                    {"role": message["role"], "content": message["content"]}
                    for message in messages
                    if message.get("role") in {"user", "assistant"} and message.get("content")
                ]
                for chat_id, messages in raw_history.items()
                if isinstance(messages, list)
            }
    except (OSError, json.JSONDecodeError, TypeError):
        return {}


def save_message(
    chat_id: str,
    role: str,
    content: str,
    directory: Path = Path("."),
) -> None:
    """Append one message using an atomic file replacement."""
    now = datetime.now()
    file_path = directory / f"history_{now:%Y-%m-%d}.json"
    temp_path = file_path.with_suffix(".json.tmp")

    with _write_lock:
        try:
            with file_path.open("r", encoding="utf-8") as file:
                data = json.load(file)
                if not isinstance(data, dict):
                    data = {}
        except (OSError, json.JSONDecodeError):
            data = {}

        data.setdefault(str(chat_id), []).append(
            {"role": role, "content": content, "time": now.strftime("%H:%M:%S")}
        )
        directory.mkdir(parents=True, exist_ok=True)
        with temp_path.open("w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
        os.replace(temp_path, file_path)
