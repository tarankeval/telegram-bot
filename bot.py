import base64
import asyncio
import logging
import requests
import random
import tempfile
from pathlib import Path

from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
from openai import OpenAI
from config import load_settings
from helpers import extract_prompt, recent_messages, wants_image
from storage import load_last_history, save_message

settings = load_settings()
GURU_CHAT_ID = settings.guru_chat_id
client = OpenAI(api_key=settings.openai_api_key)


logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

# ===== personality loader =====
def load_personality():
    try:
        with open("personality.txt", "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return "Ты — духовный наставник. Отвечай тепло, мягко и мудро."

# ===== knowledge loader =====
def load_knowledge():
    try:
        with open("knowledge.txt", "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return ""

chat_histories = load_last_history(settings.history_dir)

# ===== Вспомогательные =====
def load_lines(filename):
    try:
        with open(filename, "r", encoding="utf-8") as f:
            return [line.strip() for line in f if line.strip()]
    except OSError:
        return ["(нет данных)"]

def load_songs():
    try:
        with open("songs.txt", "r", encoding="utf-8") as f:
            return [line.strip() for line in f if line.strip()]
    except OSError:
        return []

songs = load_songs()

# --- Cartesia TTS ---
def tts_cartesia_to_file(text: str, filename: str):
    """
    Синтез речи через Cartesia в mp3 файл.
    """
    url = "https://api.cartesia.ai/tts/bytes"
    headers = {
        "X-API-Key": settings.cartesia_api_key,
        "Cartesia-Version": settings.cartesia_version,
        "Content-Type": "application/json",
    }
    payload = {
        "model_id": settings.cartesia_model_id,
        "transcript": text,
        "voice": {
            "mode": "id",
            "id": settings.cartesia_voice_id,
        },
        "output_format": {
            "container": "mp3",
            "encoding": "mp3",
            "sample_rate": 44100,
        },
        "language": "ru",
    }

    if not settings.cartesia_enabled:
        raise RuntimeError("Cartesia TTS is not configured")

    resp = requests.post(url, headers=headers, json=payload, timeout=60)
    resp.raise_for_status()
    with open(filename, "wb") as f:
        f.write(resp.content)

# ===== Handlers =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        ["🌸 Мантра дня", "🌿 Совет по практике"],
        ["📖 История из книги", "🎶 Песня дня"],
        ["🎨 Создать картинку"]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text("Напиши мне сообщение или выбери кнопку 👇", reply_markup=reply_markup)

# === 🌸 Мантра дня ===
async def handle_mantra(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    lines = load_lines("mantras.txt")
    reply = random.choice(lines) if lines else "⚠️ Файл с мантрами пуст."
    await update.message.reply_text(reply)
    await context.bot.send_message(
        chat_id=GURU_CHAT_ID,
        text=f"🌸 Мантра дня для ученика ({chat_id}):\n{reply}"
    )

# === 🌿 Совет по практике ===
async def handle_advice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    lines = load_lines("advices.txt")
    reply = random.choice(lines) if lines else "⚠️ Файл с советами пуст."
    await update.message.reply_text(reply)
    await context.bot.send_message(
        chat_id=GURU_CHAT_ID,
        text=f"🌿 Совет по практике для ученика ({chat_id}):\n{reply}"
    )

# === 📖 История из книги ===
async def handle_story(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    lines = load_lines("stories.txt")
    reply = random.choice(lines) if lines else "⚠️ Файл с историями пуст."
    await update.message.reply_text(reply)
    await context.bot.send_message(
        chat_id=GURU_CHAT_ID,
        text=f"📖 История из книги для ученика ({chat_id}):\n{reply}"
    )

# === 🎶 Песня дня ===
async def handle_song(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    if songs:
        reply = f"🎶 Песня дня: {random.choice(songs)}"
    else:
        reply = "⚠️ Список песен пуст."
    await update.message.reply_text(reply)
    await context.bot.send_message(
        chat_id=GURU_CHAT_ID,
        text=f"🎶 Песня дня для ученика ({chat_id}):\n{reply}"
    )

# === Голос ===
async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    user = update.effective_user

    fobj = await context.bot.get_file(update.message.voice.file_id)
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as temp_file:
        local_path = Path(temp_file.name)

    try:
        await fobj.download_to_drive(local_path)

        def transcribe_voice():
            with local_path.open("rb") as audio_file:
                return client.audio.transcriptions.create(
                    model="gpt-4o-mini-transcribe", file=audio_file
                )

        transcript = await asyncio.to_thread(transcribe_voice)
    finally:
        local_path.unlink(missing_ok=True)
    text = transcript.text or "(пусто)"
    save_message(chat_id, "user", f"[voice] {text}", settings.history_dir)

    # уведомим Гуру о входящем голосе
    await context.bot.send_message(
        chat_id=GURU_CHAT_ID,
        text=f"🎙️ Голос от {user.first_name} (@{user.username}):\n{text}\n(chat_id: {chat_id})"
    )

    # ответ бота
    system_message = {"role": "system", "content": load_personality() + "\n\n" + load_knowledge()}
    response = await asyncio.to_thread(
        client.chat.completions.create,
        model="gpt-4o-mini",
        messages=[system_message, {"role": "user", "content": text}],
    )
    bot_reply = response.choices[0].message.content
    save_message(chat_id, "assistant", bot_reply, settings.history_dir)

    await update.message.reply_text(bot_reply)

    # TTS через Cartesia твоим голосом
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as temp_file:
            speech_file = Path(temp_file.name)
        try:
            await asyncio.to_thread(tts_cartesia_to_file, bot_reply, str(speech_file))
            with speech_file.open("rb") as voice_file:
                await context.bot.send_voice(chat_id=update.effective_chat.id, voice=voice_file)
        finally:
            speech_file.unlink(missing_ok=True)
    except Exception as e:
        logging.warning(f"TTS error: {e}")

    # уведомим Гуру об ответе
    await context.bot.send_message(
        chat_id=GURU_CHAT_ID,
        text=f"🤖 Ответ бота (на голос) ({chat_id}):\n{bot_reply}\n\n➡️ /reply {chat_id} твой_текст"
    )

# === Фото ===
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    chat_type = chat.type
    chat_id = str(chat.id)
    user = update.effective_user

    # если не личный чат — не реагируем
    if chat_type != "private":
        return

    # если caption начинается с /reply — это отдельный обработчик
    if update.message.caption and update.message.caption.strip().startswith("/reply"):
        return

    file = await context.bot.get_file(update.message.photo[-1].file_id)
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as temp_file:
        image_path = Path(temp_file.name)

    save_message(chat_id, "user", "[photo] (изображение)", settings.history_dir)

    try:
        await file.download_to_drive(image_path)
        image_data = base64.b64encode(image_path.read_bytes()).decode("ascii")
        image_url = f"data:image/jpeg;base64,{image_data}"
        response = await asyncio.to_thread(
            client.chat.completions.create,
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "Опиши фото красиво и бережно."},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Опиши это изображение мягко и с любовью."},
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ],
                },
            ],
        )
    finally:
        image_path.unlink(missing_ok=True)
    reply = response.choices[0].message.content
    save_message(chat_id, "assistant", reply, settings.history_dir)

    await update.message.reply_text(f"🖼️ {reply}")

    # уведомим Гуру
    await context.bot.send_message(
        chat_id=GURU_CHAT_ID,
        text=f"🖼 Фото от {user.first_name} (@{user.username}), chat_id {chat_id}.\nОтвет бота:\n{reply}\n\n➡️ /reply {chat_id} твой_текст"
    )

# === Рисование ===
async def _generate_and_send_image(update, context, prompt_text: str):
    chat_id = str(update.effective_chat.id)
    user = update.effective_user

    save_message(chat_id, "user", f"[draw] {prompt_text}", settings.history_dir)
    await context.bot.send_message(
        chat_id=GURU_CHAT_ID,
        text=f"🖼 Запрос /draw от {user.first_name} (@{user.username})\n(chat_id: {chat_id})\n\nТекст: {prompt_text}"
    )

    await update.message.reply_text("🎨 Создаю образ... несколько секунд...")

    try:
        img_resp = await asyncio.to_thread(
            client.images.generate, model="gpt-image-1", prompt=prompt_text, size="1024x1024"
        )
        b64 = img_resp.data[0].b64_json
        img_bytes = base64.b64decode(b64)
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temp_file:
            out_name = Path(temp_file.name)
            temp_file.write(img_bytes)
        try:
            with out_name.open("rb") as picture:
                await context.bot.send_photo(
                    chat_id=update.effective_chat.id, photo=picture, caption="🖼 Готово."
                )
        finally:
            out_name.unlink(missing_ok=True)

        save_message(chat_id, "assistant", f"[image_generated] {prompt_text}", settings.history_dir)

        await context.bot.send_message(
            chat_id=GURU_CHAT_ID,
            text=f"🖼 Изображение создано для ({chat_id}).\nТекст: {prompt_text}\n\n➡️ /reply {chat_id} твой_текст"
        )
    except Exception as e:
        await update.message.reply_text(f"⚠️ Ошибка: {e}")

async def draw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    prompt_text = " ".join(context.args) if context.args else ""
    if not prompt_text:
        await update.message.reply_text("🖌 Использование: /draw Лотос в сиянии луны")
        return
    await _generate_and_send_image(update, context, prompt_text)

async def handle_draw_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["awaiting_draw_prompt"] = True
    await update.message.reply_text("🎨 Пришли описание картинки одним сообщением.")

# === GPT чат ===
async def chat_with_gpt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = str(update.effective_chat.id)
    user_message = update.message.text or ""

    # если нажата кнопка «Создать картинку» — следующее сообщение используем как промпт
    if context.user_data.get("awaiting_draw_prompt") and user_message and not user_message.startswith("/"):
        context.user_data["awaiting_draw_prompt"] = False
        await _generate_and_send_image(update, context, user_message)
        return

    # авто-рисование по естественному тексту
    if wants_image(user_message):
        prompt = extract_prompt(user_message)
        if not prompt or len(prompt) < 4:
            await update.message.reply_text("🎨 Опиши чуть подробнее, что рисовать (стиль/цвета/атмосфера).")
            return
        await _generate_and_send_image(update, context, prompt)
        return

    # лог + форвард в Гуру
    chat_histories.setdefault(chat_id, []).append({"role": "user", "content": user_message})
    save_message(chat_id, "user", user_message, settings.history_dir)
    forward_text = f"❓ Вопрос от {user.first_name} (@{user.username}):\n{user_message}\n(chat_id: {chat_id})"
    await context.bot.send_message(chat_id=GURU_CHAT_ID, text=forward_text)

    # ответ с учётом личности/знаний + истории
    system_message = {"role": "system", "content": load_personality() + "\n\n" + load_knowledge()}
    response = await asyncio.to_thread(
        client.chat.completions.create,
        model="gpt-4o-mini",
        messages=[system_message] + recent_messages(chat_histories[chat_id]),
    )
    bot_reply = response.choices[0].message.content

    chat_histories[chat_id].append({"role": "assistant", "content": bot_reply})
    save_message(chat_id, "assistant", bot_reply, settings.history_dir)

    # текст + TTS голосом
    await update.message.reply_text(bot_reply)
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as temp_file:
            speech_file = Path(temp_file.name)
        try:
            await asyncio.to_thread(tts_cartesia_to_file, bot_reply, str(speech_file))
            with speech_file.open("rb") as voice_file:
                await context.bot.send_voice(chat_id=update.effective_chat.id, voice=voice_file)
        finally:
            speech_file.unlink(missing_ok=True)
    except Exception as e:
        logging.warning(f"TTS error: {e}")

    # уведомим Гуру об ответе
    await context.bot.send_message(
        chat_id=GURU_CHAT_ID,
        text=f"🤖 Ответ бота ученику ({chat_id}):\n{bot_reply}\n\n➡️ /reply {chat_id} твой_текст"
    )

# --- Групповой чат (бот отвечает только при упоминании @username) ---
async def group_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    bot_username = context.bot.username
    user = update.effective_user

    if f"@{bot_username}" not in text:
        return

    user_message = text.replace(f"@{bot_username}", "").strip()
    chat_id = str(update.effective_chat.id)

    system_message = {"role": "system", "content": load_personality() + "\n\n" + load_knowledge()}
    response = await asyncio.to_thread(
        client.chat.completions.create,
        model="gpt-4o-mini",
        messages=[system_message, {"role": "user", "content": user_message}],
        temperature=0.8,
    )

    bot_reply = response.choices[0].message.content
    await update.message.reply_text(f"{user.first_name}, {bot_reply}", reply_to_message_id=update.message.message_id)

    await context.bot.send_message(
        chat_id=GURU_CHAT_ID,
        text=f"🤖 Ответ бота в группе ({chat_id}):\n{bot_reply}\n\n➡️ /reply {chat_id} твой_текст"
    )

# --- Ответ Гуру (с возможностью фото) ---
async def reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отправка послания ученику, с возможностью прикрепить фото."""
    if update.effective_chat.id != GURU_CHAT_ID:
        await update.message.reply_text("Эта команда доступна только Гуру.")
        return

    caption_text = update.message.caption or update.message.text or ""
    args = caption_text.split()
    if len(args) < 2:
        await update.message.reply_text("⚠️ Использование: /reply CHAT_ID [текст]")
        return

    target_chat_id = args[1] if args[0] == "/reply" else args[0]
    reply_text = " ".join(args[2:]) if args[0] == "/reply" and len(args) > 2 else " ".join(args[1:])

    # === Отправка ===
    if update.message.photo:
        photo = update.message.photo[-1]
        caption = reply_text if reply_text else "📩 Трансцендентное изображение"

        # Если подпись слишком длинная — разделим
        if len(caption) > 1024:
            short_caption = caption[:1000] + "…"
            await context.bot.send_photo(chat_id=target_chat_id, photo=photo.file_id, caption=short_caption)
            await context.bot.send_message(chat_id=target_chat_id, text=caption)
        else:
            await context.bot.send_photo(chat_id=target_chat_id, photo=photo.file_id, caption=caption)

        await update.message.reply_text("✅ Фото и послание отправлены ученику.")
    else:
        await context.bot.send_message(chat_id=target_chat_id, text=f"📩 Трансцендентный ответ:\n{reply_text}")
        await update.message.reply_text("✅ Ответ отправлен ученику.")


async def handle_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logging.exception("Unhandled Telegram update error", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        await update.effective_message.reply_text(
            "⚠️ Не удалось обработать сообщение. Попробуйте ещё раз немного позже."
        )


# ===== Main =====
def main():
    if not settings.cartesia_enabled:
        logging.warning("Cartesia is not fully configured; voice replies will be disabled")

    app = ApplicationBuilder().token(settings.telegram_token).build()
    app.add_error_handler(handle_error)

    # Команды
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("draw", draw))
    app.add_handler(CommandHandler("reply", reply))

    # /reply c фото в caption
    app.add_handler(MessageHandler(filters.PHOTO & filters.CaptionRegex(r"^/reply"), reply))

    # Кнопки
    app.add_handler(MessageHandler(filters.Regex(r"^🌸 Мантра дня$"), handle_mantra))
    app.add_handler(MessageHandler(filters.Regex(r"^🌿 Совет по практике$"), handle_advice))
    app.add_handler(MessageHandler(filters.Regex(r"^📖 История из книги$"), handle_story))
    app.add_handler(MessageHandler(filters.Regex(r"^🎶 Песня дня$"), handle_song))
    app.add_handler(MessageHandler(filters.Regex(r"^🎨 Создать картинку$"), handle_draw_button))

    # Группы и личка
    app.add_handler(MessageHandler(filters.TEXT & filters.ChatType.GROUPS, group_chat))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & filters.ChatType.PRIVATE, chat_with_gpt))

    print("🤖 Бот запущен и принимает команды.")
    app.run_polling()

if __name__ == "__main__":
    main()
