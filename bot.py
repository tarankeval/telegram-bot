import os
import json
import base64
import logging
import requests
import random
from datetime import datetime

from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
from openai import OpenAI

# ================================
# 🔑 КЛЮЧИ (ВСТАВЬ СВОИ)
# ================================
TELEGRAM_TOKEN = "(ВСТАВЬ СВОИ)"
OPENAI_API_KEY = "(ВСТАВЬ СВОИ)"

# Cartesia
CARTESIA_API_KEY = "(ВСТАВЬ СВОИ)"      # sk_car_...
CARTESIA_VOICE_ID = "(ВСТАВЬ СВОИ)"    
CARTESIA_MODEL_ID = "sonic-3"
CARTESIA_VERSION = "2025-04-16"

GURU_CHAT_ID = 642590466

client = OpenAI(api_key=OPENAI_API_KEY)

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

# ===== История =====
def load_last_history():
    files = [f for f in os.listdir() if f.startswith("history_") and f.endswith(".json")]
    if not files:
        return {}
    files.sort()
    last_file = files[-1]
    try:
        with open(last_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

chat_histories = load_last_history()

def save_message(chat_id, role, content):
    today = datetime.now().strftime("%Y-%m-%d")
    file_path = f"history_{today}.json"
    if os.path.exists(file_path):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except:
            data = {}
    else:
        data = {}
    data.setdefault(str(chat_id), []).append({
        "role": role,
        "content": content,
        "time": datetime.now().strftime("%H:%M:%S")
    })
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ===== Вспомогательные =====
def load_lines(filename):
    try:
        with open(filename, "r", encoding="utf-8") as f:
            return [line.strip() for line in f if line.strip()]
    except:
        return ["(нет данных)"]

def load_songs():
    try:
        with open("songs.txt", "r", encoding="utf-8") as f:
            return [line.strip() for line in f if line.strip()]
    except:
        return []

songs = load_songs()

# --- Cartesia TTS ---
def tts_cartesia_to_file(text: str, filename: str):
    """
    Синтез речи через Cartesia в mp3 файл.
    """
    url = "https://api.cartesia.ai/tts/bytes"
    headers = {
        "X-API-Key": CARTESIA_API_KEY,
        "Cartesia-Version": CARTESIA_VERSION,
        "Content-Type": "application/json",
    }
    payload = {
        "model_id": CARTESIA_MODEL_ID,
        "transcript": text,
        "voice": {
            "mode": "id",
            "id": CARTESIA_VOICE_ID,
        },
        "output_format": {
            "container": "mp3",
            "encoding": "mp3",
            "sample_rate": 44100,
        },
        "language": "ru",
    }

    resp = requests.post(url, headers=headers, json=payload)
    resp.raise_for_status()
    with open(filename, "wb") as f:
        f.write(resp.content)

# --- Авто-триггер рисования по тексту ---
DRAW_TRIGGERS = [
    # 🇷🇺 Русские
    "нарисуй",
    "изобрази",
    "создай картинку",
    "создай изображение",
    "сделай рисунок",
    "сотвори картину",
    "generate image",
    "draw picture",
    "create artwork",
    "сделай арт",
    "artwork",

    # 🇬🇧 Английские
    "draw", "drawing", "can you draw", "please draw", "sketch", "picture",
    "image", "generate image", "make image", "make a picture", "create image",
    "illustration", "art", "artwork", "paint", "painting", "render", "design",

    # 🇨🇿 Чешские
    "nakresli", "můžeš nakreslit", "obrázek", "obraz", "ilustrace", "kresba",
    "udělej obrázek", "vytvoř obrázek", "generuj obrázek",

    # 🇺🇦 Украинские
    "намалюй", "зроби картинку", "малюнок", "зобрази", "створи образ",

    # Доп. синонимы
    "арт картинка", "цифровое искусство", "digital art", "rendering", "concept art",
    "sketching", "visualize", "visualization", "show me", "покажи изображение"
]

def wants_image(text: str) -> bool:
    if not text:
        return False
    return any(w in text.lower() for w in DRAW_TRIGGERS)

def extract_prompt(text: str) -> str:
    if not text:
        return ""
    t = text.strip()
    tl = t.lower()
    starts = ["нарисуй", "создай", "сделай", "сгенерируй", "изобрази", "сотвори", "draw", "make", "generate"]
    for w in starts:
        if tl.startswith(w):
            return t[len(w):].lstrip(" :,-—")
    return t

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
    local_path = f"voice_{chat_id}.ogg"
    await fobj.download_to_drive(local_path)

    with open(local_path, "rb") as f:
        transcript = client.audio.transcriptions.create(model="gpt-4o-mini-transcribe", file=f)
    text = transcript.text or "(пусто)"
    save_message(chat_id, "user", f"[voice] {text}")

    # уведомим Гуру о входящем голосе
    await context.bot.send_message(
        chat_id=GURU_CHAT_ID,
        text=f"🎙️ Голос от {user.first_name} (@{user.username}):\n{text}\n(chat_id: {chat_id})"
    )

    # ответ бота
    system_message = {"role": "system", "content": load_personality() + "\n\n" + load_knowledge()}
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[system_message, {"role": "user", "content": text}]
    )
    bot_reply = response.choices[0].message.content
    save_message(chat_id, "assistant", bot_reply)

    await update.message.reply_text(bot_reply)

    # TTS через Cartesia твоим голосом
    try:
        speech_file = f"reply_{chat_id}.mp3"
        tts_cartesia_to_file(bot_reply, speech_file)
        with open(speech_file, "rb") as vf:
            await context.bot.send_voice(chat_id=update.effective_chat.id, voice=vf)
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
    image_url = file.file_path

    save_message(chat_id, "user", "[photo] (изображение)")

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "Опиши фото красиво и бережно."},
            {"role": "user",
             "content": [
                {"type": "text", "text": "Опиши это изображение мягко и с любовью."},
                {"type": "image_url", "image_url": {"url": image_url}}
             ]}
        ]
    )
    reply = response.choices[0].message.content
    save_message(chat_id, "assistant", reply)

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

    save_message(chat_id, "user", f"[draw] {prompt_text}")
    await context.bot.send_message(
        chat_id=GURU_CHAT_ID,
        text=f"🖼 Запрос /draw от {user.first_name} (@{user.username})\n(chat_id: {chat_id})\n\nТекст: {prompt_text}"
    )

    await update.message.reply_text("🎨 Создаю образ... несколько секунд...")

    try:
        img_resp = client.images.generate(model="gpt-image-1", prompt=prompt_text, size="1024x1024")
        b64 = img_resp.data[0].b64_json
        img_bytes = base64.b64decode(b64)
        out_name = f"draw_{int(datetime.now().timestamp())}.png"
        with open(out_name, "wb") as f:
            f.write(img_bytes)
        with open(out_name, "rb") as pic:
            await context.bot.send_photo(chat_id=update.effective_chat.id, photo=pic, caption="🖼 Готово.")

        save_message(chat_id, "assistant", f"[image_generated] {prompt_text}")

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
    save_message(chat_id, "user", user_message)
    forward_text = f"❓ Вопрос от {user.first_name} (@{user.username}):\n{user_message}\n(chat_id: {chat_id})"
    await context.bot.send_message(chat_id=GURU_CHAT_ID, text=forward_text)

    # ответ с учётом личности/знаний + истории
    system_message = {"role": "system", "content": load_personality() + "\n\n" + load_knowledge()}
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[system_message] + chat_histories[chat_id]
    )
    bot_reply = response.choices[0].message.content

    chat_histories[chat_id].append({"role": "assistant", "content": bot_reply})
    save_message(chat_id, "assistant", bot_reply)

    # текст + TTS голосом
    await update.message.reply_text(bot_reply)
    try:
        speech_file = f"reply_{chat_id}.mp3"
        tts_cartesia_to_file(bot_reply, speech_file)
        with open(speech_file, "rb") as vf:
            await context.bot.send_voice(chat_id=update.effective_chat.id, voice=vf)
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
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[system_message, {"role": "user", "content": user_message}],
        temperature=0.8
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

# ===== Main =====
def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

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


