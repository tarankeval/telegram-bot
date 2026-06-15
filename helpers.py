"""Pure helper functions used by the bot and tests."""

from __future__ import annotations


DRAW_TRIGGERS = (
    "нарисуй", "изобрази", "создай картинку", "создай изображение",
    "сделай рисунок", "сотвори картину", "generate image", "draw picture",
    "create artwork", "сделай арт", "draw", "drawing", "can you draw",
    "please draw", "sketch", "picture", "image", "make image",
    "make a picture", "create image", "illustration", "art", "artwork",
    "paint", "painting", "render", "design", "nakresli", "můžeš nakreslit",
    "obrázek", "obraz", "ilustrace", "kresba", "udělej obrázek",
    "vytvoř obrázek", "generuj obrázek", "намалюй", "зроби картинку",
    "малюнок", "зобрази", "створи образ", "арт картинка",
    "цифровое искусство", "digital art", "rendering", "concept art",
    "sketching", "visualize", "visualization", "show me", "покажи изображение",
)


def wants_image(text: str) -> bool:
    return bool(text) and any(trigger in text.lower() for trigger in DRAW_TRIGGERS)


def extract_prompt(text: str) -> str:
    if not text:
        return ""

    stripped = text.strip()
    lowered = stripped.lower()
    prefixes = (
        "нарисуй", "создай", "сделай", "сгенерируй", "изобрази",
        "сотвори", "draw", "make", "generate",
    )
    for prefix in prefixes:
        if lowered.startswith(prefix):
            return stripped[len(prefix):].lstrip(" :,-—")
    return stripped


def recent_messages(messages: list[dict[str, str]], limit: int = 30) -> list[dict[str, str]]:
    """Return a bounded OpenAI-compatible conversation context."""
    return [
        {"role": message["role"], "content": message["content"]}
        for message in messages[-limit:]
        if message.get("role") in {"user", "assistant"} and message.get("content")
    ]
