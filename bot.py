import os
import json
import base64
import logging
import requests
import random
import asyncio
import re
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo
from telegram import Update, ReplyKeyboardMarkup, BotCommand
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
from openai import OpenAI

# ГЛОБАЛЬНОЕ ХРАНИЛИЩЕ АКТИВНЫХ НАПОМИНАНИЙ (старые async-задачи)
reminder_tasks = {}

# Отложенные напоминания "на дату и время" (для /remind_at)
# chat_id (str) -> list[dict], каждый dict:
# {"date": "DD.MM.YYYY", "time": "HH:MM", "text": "...", "pre_job_name": "...", "main_job_name": "..."}
AT_REMINDERS_FILE = "reminders_at.json"
at_reminders = {}

# ==== ИЗБРАННОЕ ====
FAVORITES_FILE = "favorites.json"
favorites = {}          # chat_id (str) -> list[{"text": "...", "created": "..."}]
last_bot_messages = {}  # chat_id (int) -> последний ответ бота (для быстрого /fav)


def load_favorites():
    """Загружаем избранное из файла."""
    global favorites
    if not os.path.exists(FAVORITES_FILE):
        favorites = {}
        return
    try:
        with open(FAVORITES_FILE, "r", encoding="utf-8") as f:
            favorites = json.load(f)
    except Exception:
        favorites = {}


def save_favorites():
    """Сохраняем избранное в файл."""
    try:
        with open(FAVORITES_FILE, "w", encoding="utf-8") as f:
            json.dump(favorites, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def remember_bot_message(chat_id: int, text: str):
    """Запоминаем последний ответ бота для /fav без реплая."""
    if not text:
        return
    last_bot_messages[chat_id] = text


# поднимаем избранное при старте
load_favorites()

def load_at_reminders():
    """Загрузить отложенные напоминания из файла."""
    global at_reminders
    if not os.path.exists(AT_REMINDERS_FILE):
        at_reminders = {}
        return
    try:
        with open(AT_REMINDERS_FILE, "r", encoding="utf-8") as f:
            at_reminders = json.load(f)
    except Exception:
        at_reminders = {}


def save_at_reminders():
    """Сохранить отложенные напоминания в файл."""
    try:
        with open(AT_REMINDERS_FILE, "w", encoding="utf-8") as f:
            json.dump(at_reminders, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# сразу поднимаем отложенные напоминания при старте
load_at_reminders()


# 🔑 Ключи берём из окружения
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY   = os.getenv("OPENAI_API_KEY")
CARTESIA_API_KEY = os.getenv("CARTESIA_API_KEY")
CARTESIA_VOICE_ID = "e9553877-3090-4f97-85df-6aaed30e047f"    # e9553877-3090-4f97-85df-6aaed30e047f
CARTESIA_MODEL_ID = "sonic-3"
CARTESIA_VERSION = "2025-04-16"


GURU_CHAT_ID = 642590466  # это можно оставить как есть
# ==== МНОГОДИАЛОГОВАЯ ПАМЯТЬ ====
DEFAULT_DIALOG_ID = "default"

DIALOG_TITLES = {
    "default": "🟣 Общий поток",
    "soul":    "🧡 Душа",
    "yoga":    "🧘 Йога",
    "tech":    "💻 Техника",
    "music":   "🎵 Творчество",
}

def get_active_dialog_id(context) -> str:
    """Текущий выбранный диалог для этого пользователя."""
    return context.user_data.get("dialog_id", DEFAULT_DIALOG_ID)

def get_active_dialog_title(context) -> str:
    dlg = get_active_dialog_id(context)
    return DIALOG_TITLES.get(dlg, dlg)

client = OpenAI(api_key=OPENAI_API_KEY)


logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
# ================================
# 🔧 ЛОАДЕРЫ / ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ================================
def load_personality():
    """Загружаем характер бота."""
    try:
        with open("personality.txt", "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return "Ты — духовный наставник. Отвечай тепло, мягко и мудро."


def load_knowledge():
    """Загружаем базу знаний бота."""
    try:
        with open("knowledge.txt", "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return ""


def load_lines(filename):
    """Загружает строки из файла (мантры, советы, истории)."""
    try:
        with open(filename, "r", encoding="utf-8") as f:
            return [line.strip() for line in f if line.strip()]
    except FileNotFoundError:
        return []


def load_songs():
    """Загружаем список песен."""
    try:
        with open("songs.txt", "r", encoding="utf-8") as f:
            return [line.strip() for line in f if line.strip()]
    except FileNotFoundError:
        return []


songs = load_songs()


# ================================
# 📜 ИСТОРИЯ СООБЩЕНИЙ
# ================================
def load_last_history():
    """
    Подгружаем последний файл истории history_YYYY-MM-DD.json.
    Приводим к формату:
    {
      "chat_id": {
        "dialog_id": [ {role, content, time}, ... ]
      }
    }
    Старый формат (список) заворачиваем в dialog "default".
    """
    files = [f for f in os.listdir() if f.startswith("history_") and f.endswith(".json")]
    if not files:
        return {}

    files.sort()
    last_file = files[-1]

    try:
        with open(last_file, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        return {}

    normalized = {}

    for chat_id, value in raw.items():
        # старый формат: просто список сообщений
        if isinstance(value, list):
            normalized[chat_id] = {
                DEFAULT_DIALOG_ID: value
            }
        # новый формат: уже словарь диалогов
        elif isinstance(value, dict):
            normalized[chat_id] = value
        else:
            # непонятный формат — пропустим
            continue

    return normalized

chat_histories = load_last_history()


def save_message(chat_id, role, content, dialog_id: str = DEFAULT_DIALOG_ID):
    """
    Сохраняем сообщение в history_YYYY-MM-DD.json
    в формате с поддержкой нескольких диалогов.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    file_path = f"history_{today}.json"
    chat_key = str(chat_id)

    # Загружаем текущий файл
    if os.path.exists(file_path):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
    else:
        data = {}

    entry = data.get(chat_key)

    # Приводим к формату "chat_id -> {dialog_id: [ ... ]}"
    if entry is None:
        entry = {}
    elif isinstance(entry, list):
        # старый формат — одна общая история
        entry = {DEFAULT_DIALOG_ID: entry}
    elif not isinstance(entry, dict):
        entry = {}

    # Берём конкретный диалог
    dialog_list = entry.setdefault(dialog_id, [])
    dialog_list.append(
        {
            "role": role,
            "content": content,
            "time": datetime.now().strftime("%H:%M:%S"),
        }
    )

    data[chat_key] = entry

    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ===== ТАЙМЗОНЫ ПОЛЬЗОВАТЕЛЕЙ =====
TIMEZONES_FILE = "timezones.json"
user_timezones = {}  # chat_id -> "Europe/Prague" и т.п.

def load_timezones():
    """Загружаем сохранённые таймзоны учеников."""
    global user_timezones
    if os.path.exists(TIMEZONES_FILE):
        try:
            with open(TIMEZONES_FILE, "r", encoding="utf-8") as f:
                user_timezones = json.load(f)
        except Exception:
            user_timezones = {}
    else:
        user_timezones = {}

def save_timezones():
    """Сохраняем таймзоны учеников."""
    try:
        with open(TIMEZONES_FILE, "w", encoding="utf-8") as f:
            json.dump(user_timezones, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def normalize_tz_name(raw: str) -> str:
    """
    Позволяет писать по-человечески: 'Прага', 'Москва', 'Минск', 'Индия' и т.п.
    Возвращаем системное имя таймзоны.
    """
    key = raw.strip().lower()
    mapping = {
        "прага": "Europe/Prague",
        "чехия": "Europe/Prague",
        "czech": "Europe/Prague",

        "москва": "Europe/Moscow",
        "moscow": "Europe/Moscow",
        "россия": "Europe/Moscow",

        "минск": "Europe/Minsk",
        "belarus": "Europe/Minsk",
        "беларусь": "Europe/Minsk",

        "киев": "Europe/Kiev",
        "kyiv": "Europe/Kiev",
        "украина": "Europe/Kiev",

        "алматы": "Asia/Almaty",
        "казахстан": "Asia/Almaty",

        "индия": "Asia/Kolkata",
        "india": "Asia/Kolkata",
        "дели": "Asia/Kolkata",
        "delhi": "Asia/Kolkata",
    }
    return mapping.get(key, raw.strip())

def get_user_timezone(chat_id: int) -> ZoneInfo:
    """Получаем таймзону ученика, по умолчанию — Прага."""
    tz_name = user_timezones.get(str(chat_id), "Europe/Prague")
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return ZoneInfo("Europe/Prague")

# сразу поднимаем таймзоны при старте
load_timezones()


# ===== НАПОМИНАНИЯ =====
REMINDERS_FILE = "reminders.json"
user_reminders = {}  # chat_id -> list[dict]

REMINDERS_DAILY_FILE = "reminders_daily.json"


def load_daily_reminders():
    """Загрузить ежедневные напоминания из файла."""
    if not os.path.exists(REMINDERS_DAILY_FILE):
        return {}
    try:
        with open(REMINDERS_DAILY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_daily_reminders():
    """Сохранить ежедневные напоминания в файл."""
    try:
        with open(REMINDERS_DAILY_FILE, "w", encoding="utf-8") as f:
            json.dump(daily_reminders, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# chat_id -> список напоминаний
# каждое: {"hour": int, "minute": int, "text": str, "job_name": str}
daily_reminders = load_daily_reminders()


def load_reminders():
    global user_reminders
    if os.path.exists(REMINDERS_FILE):
        try:
            with open(REMINDERS_FILE, "r", encoding="utf-8") as f:
                user_reminders = json.load(f)
        except Exception:
            user_reminders = {}
    else:
        user_reminders = {}


def save_reminders():
    with open(REMINDERS_FILE, "w", encoding="utf-8") as f:
        json.dump(user_reminders, f, ensure_ascii=False, indent=2)


async def send_reminder(context: ContextTypes.DEFAULT_TYPE):
    text = context.job.data
    chat_id = context.job.chat_id
    await context.bot.send_message(chat_id=chat_id, text=f"🔔 Напоминание:\n{text}")


async def handle_reminder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Понимает:
    1) 'напомни мне в 07:30 выпей воды'         -> одноразовое
    2) 'каждый день в 07:00 утренняя медитация' -> ежедневное
    """
    chat_id = str(update.effective_chat.id)
    text_raw = update.message.text.strip()
    text = text_raw.lower()

    job_queue = context.application.job_queue

    # --- ЕЖЕДНЕВНО ---
    m_daily = re.match(r"^(каждый день|ежедневно)\s+в\s+(\d{1,2}):(\d{2})\s+(.+)$", text)
    if m_daily:
        hour = int(m_daily.group(2))
        minute = int(m_daily.group(3))
        # оригинальный текст после времени
        msg = text_raw.split(" ", 4)[-1]

        # используем таймзону ученика
        tz = get_user_timezone(int(chat_id))
        now = datetime.now(tz)
        first_run = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if first_run <= now:
            first_run += timedelta(days=1)

        delay = (first_run - now).total_seconds()

        job_name = f"daily_{chat_id}_{hour:02d}{minute:02d}_{int(now.timestamp())}"

        job_queue.run_repeating(
            send_reminder,
            interval=86400,  # раз в сутки
            first=delay,     # через сколько секунд первый запуск
            chat_id=int(chat_id),
            data=msg,
            name=job_name,
        )

        user_reminders.setdefault(chat_id, []).append(
            {
                "type": "daily",
                "time": f"{hour:02d}:{minute:02d}",
                "text": msg,
                "job_name": job_name,
            }
        )
        save_reminders()

        await update.message.reply_text(
            f"✅ Ежедневное напоминание запланировано.\n"
            f"Время: {hour:02d}:{minute:02d}\n"
            f"Текст: {msg}"
        )
        return

    # --- ОДНОРАЗОВО ---
    m_once = re.match(r"^напомни( мне)?\s+в\s+(\d{1,2}):(\d{2})\s+(.+)$", text)
    if m_once:
        hour = int(m_once.group(2))
        minute = int(m_once.group(3))
        msg = text_raw.split(" ", 4)[-1]

        tz = get_user_timezone(int(chat_id))
        now = datetime.now(tz)
        when = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if when <= now:
            when += timedelta(days=1)

        delay = (when - now).total_seconds()
        job_name = f"once_{chat_id}_{int(when.timestamp())}"

        job_queue.run_once(
            send_reminder,
            when=delay,  # через сколько секунд отправить
            chat_id=int(chat_id),
            data=msg,
            name=job_name,
        )

        user_reminders.setdefault(chat_id, []).append(
            {
                "type": "once",
                "when": when.isoformat(),
                "text": msg,
                "job_name": job_name,
            }
        )
        save_reminders()

        await update.message.reply_text(
            f"✅ Напоминание запланировано на {when.strftime('%Y-%m-%d %H:%M')}.\n"
            f"Текст: {msg}"
        )
        return

    # Если не узнал формат
    await update.message.reply_text(
        "Пока я понимаю два формата:\n"
        "• «напомни мне в 07:30 выпей воды»\n"
        "• «каждый день в 07:00 утренняя медитация»"
    )

async def start_reminders(app):
    """
    Вызывается при старте приложения, чтобы восстановить все напоминания
    из файла REMINDERS_FILE (естественные фразы типа
    «каждый день в 07:00 ...» и «напомни мне в 16:30 ...»),
    а также сразу настроить меню команд бота.
    """
    load_reminders()

    job_queue = app.job_queue

    for chat_id, items in user_reminders.items():
        try:
            chat_int = int(chat_id)
        except ValueError:
            continue

        tz = get_user_timezone(chat_int)
        now = datetime.now(tz)

        for rem in items:
            rtype = rem.get("type")

            # ---- ОДНОРАЗОВОЕ ----
            if rtype == "once":
                when_str = rem.get("when")
                text = rem.get("text", "Напоминание 🌱")
                if not when_str:
                    continue

                try:
                    target = datetime.fromisoformat(when_str)
                except Exception:
                    continue

                # приводим к таймзоне пользователя
                if target.tzinfo is None:
                    target = target.replace(tzinfo=tz)
                else:
                    target = target.astimezone(tz)

                if target <= now:
                    # уже в прошлом — не восстанавливаем
                    continue

                delay = (target - now).total_seconds()
                job_name = rem.get("job_name") or f"once_{chat_id}_{int(target.timestamp())}"

                job_queue.run_once(
                    send_reminder,
                    when=delay,
                    chat_id=chat_int,
                    data=text,
                    name=job_name,
                )

            # ---- ЕЖЕДНЕВНОЕ (через 'каждый день ...') ----
            elif rtype == "daily":
                time_str = rem.get("time")
                text = rem.get("text", "Напоминание 🌱")
                if not time_str:
                    continue

                try:
                    h, m = map(int, time_str.split(":"))
                except Exception:
                    continue

                first_run = now.replace(hour=h, minute=m, second=0, microsecond=0)
                if first_run <= now:
                    first_run += timedelta(days=1)

                delay = (first_run - now).total_seconds()
                job_name = rem.get("job_name") or f"daily_{chat_id}_{h:02d}{m:02d}_{int(now.timestamp())}"

                job_queue.run_repeating(
                    send_reminder,
                    interval=86400,   # раз в сутки
                    first=delay,      # через сколько секунд первый запуск
                    chat_id=chat_int,
                    data=text,
                    name=job_name,
                )

    # 🎛 Настраиваем меню команд бота
    await app.bot.set_my_commands([
        BotCommand("start",        "Приветствие и клавиатура"),
        BotCommand("mantra",       "🌸 Мантра дня"),
        BotCommand("advice",       "🌿 Совет по практике"),
        BotCommand("story",        "📖 История из книги"),
        BotCommand("song",         "🎶 Песня дня"),
        BotCommand("draw",         "🎨 Создать картинку"),

        BotCommand("remind_once",  "⏰ Одноразовое напоминание"),
        BotCommand("daily",        "🔁 Ежедневное напоминание"),
        BotCommand("remind_at",    "📅 Напоминание на дату и время"),
        BotCommand("reminders",    "📝 Список напоминаний"),
        BotCommand("cancel_reminder", "❌ Отменить напоминание по номеру"),

        BotCommand("fav",          "⭐ Сохранить в избранное"),
        BotCommand("favorites",    "🌟 Показать избранное"),
        BotCommand("fav_show",     "📜 Показать пункт избранного полностью"),
        BotCommand("fav_del",      "🗑️ Удалить пункт избранного"),

        # 👉 Диалоги
        BotCommand("dialog",         "💬 Выбрать/посмотреть активный диалог"),
        BotCommand("dialog_default", "🟣 Диалог: Общий поток"),
        BotCommand("dialog_soul",    "🧡 Диалог: Душа"),
        BotCommand("dialog_yoga",    "🧘 Диалог: Йога"),
        BotCommand("dialog_tech",    "💻 Диалог: Техника"),
        BotCommand("dialog_music",   "🎵 Диалог: Творчество"),

        BotCommand("set_timezone", "🕒 Настроить часовой пояс"),
    ])

async def daily_job_callback(context: ContextTypes.DEFAULT_TYPE):
    """То, что реально отправляет сообщение каждый день."""
    chat_id = context.job.chat_id
    data = context.job.data or {}
    text = data.get("text", "Напоминание 🌱")
    await context.bot.send_message(chat_id=chat_id, text=f"⏰ {text}")
# Колбэки для отложенных напоминаний /remind_at
async def at_pre_job(context: ContextTypes.DEFAULT_TYPE):
    """Напоминание за час до события."""
    chat_id = context.job.chat_id
    data = context.job.data or {}
    text = data.get("text", "Напоминание 🌱")
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"⏰ Через час:\n{text}"
    )


async def at_main_job(context: ContextTypes.DEFAULT_TYPE):
    """Напоминание в момент события."""
    chat_id = context.job.chat_id
    data = context.job.data or {}
    text = data.get("text", "Напоминание 🌱")
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"🔔 Напоминание (по дате):\n{text}"
    )

async def scheduled_restart_job(context: ContextTypes.DEFAULT_TYPE):
    """
    Ежедневная техническая перезагрузка:
    - пишет в Guru-чат служебное сообщение
    - мягко завершает процесс, systemd поднимает бота заново
    """
    prague_tz = ZoneInfo("Europe/Prague")
    now_str = datetime.now(prague_tz).strftime("%Y-%m-%d %H:%M")

    try:
        await context.bot.send_message(
            chat_id=GURU_CHAT_ID,
            text=(
                "🔧 Техническая перезагрузка бота.\n"
                f"Время (Прага): {now_str}\n"
                "Через пару секунд я поднимусь снова 🙏"
            ),
        )
    except Exception as e:
        logging.warning(f"Error sending restart notice: {e}")

    # Даём телеграму отправить сообщение и аккуратно выходим
    await asyncio.sleep(2)
    os._exit(0)

def restore_daily_jobs(app):
    """При старте бота восстановить все ежедневные напоминания из файла."""
    job_queue = app.job_queue

    for chat_id_str, items in daily_reminders.items():
        try:
            chat_id = int(chat_id_str)
        except ValueError:
            continue

        for item in items:
            hour = item.get("hour")
            minute = item.get("minute")
            text = item.get("text", "Напоминание 🌱")

            if hour is None or minute is None:
                continue

            job_name = item.get("job_name")
            if not job_name:
                job_name = f"daily-{chat_id}-{hour:02d}{minute:02d}-{abs(hash(text))%10000}"
                item["job_name"] = job_name  # дописываем в память

            job_queue.run_daily(
                daily_job_callback,
                time=time(hour=hour, minute=minute),
                chat_id=chat_id,
                name=job_name,
                data={"text": text},
            )

    # на случай, если мы дописали поля job_name
    save_daily_reminders()

def restore_at_jobs(app):
    """При старте бота восстановить все отложенные напоминания /remind_at из файла."""
    job_queue = app.job_queue

    for chat_id_str, items in at_reminders.items():
        try:
            chat_id = int(chat_id_str)
        except ValueError:
            continue

        tz = get_user_timezone(chat_id)
        now = datetime.now(tz)

        for item in items:
            date_str = item.get("date")
            time_str = item.get("time")
            text = item.get("text", "Напоминание 🌱")

            if not date_str or not time_str:
                continue

            # Пытаемся распарсить дату в формате DD.MM.YYYY, а на всякий случай и YYYY-MM-DD
            target = None
            try:
                d = datetime.strptime(date_str, "%d.%m.%Y")
                h, m = map(int, time_str.split(":"))
                target = datetime(
                    year=d.year,
                    month=d.month,
                    day=d.day,
                    hour=h,
                    minute=m,
                    second=0,
                    microsecond=0,
                    tzinfo=tz,
                )
            except Exception:
                try:
                    d = datetime.strptime(date_str, "%Y-%m-%d")
                    h, m = map(int, time_str.split(":"))
                    target = datetime(
                        year=d.year,
                        month=d.month,
                        day=d.day,
                        hour=h,
                        minute=m,
                        second=0,
                        microsecond=0,
                        tzinfo=tz,
                    )
                except Exception:
                    continue

            if target <= now:
                # событие в прошлом — не восстанавливаем
                continue

            main_delay = (target - now).total_seconds()

            pre_time = target - timedelta(hours=1)
            pre_delay = max((pre_time - now).total_seconds(), 0)

            # Имена jobs, чтобы можно было отменять
            ts_key = int(target.timestamp())
            pre_job_name = f"at-pre-{chat_id}-{ts_key}"
            main_job_name = f"at-main-{chat_id}-{ts_key}"

            # Запускаем jobs
            job_queue.run_once(
                at_pre_job,
                when=pre_delay,
                chat_id=chat_id,
                name=pre_job_name,
                data={"text": text},
            )
            job_queue.run_once(
                at_main_job,
                when=main_delay,
                chat_id=chat_id,
                name=main_job_name,
                data={"text": text},
            )

            # Обновляем запись
            item["pre_job_name"] = pre_job_name
            item["main_job_name"] = main_job_name

    # Сохраняем обновлённые job_name обратно
    save_at_reminders()
async def daily_reminder_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /daily 07:00 утренняя медитация
    /daily 21:30 вечерняя мантра
    """
    chat_id = update.effective_chat.id

    if not context.args:
        await update.message.reply_text(
            "Формат:\n"
            "/daily 07:00 утренняя медитация\n"
            "/daily 21:30 вечерняя мантра"
        )
        return

    raw = " ".join(context.args)

    # Ищем время формата ЧЧ:ММ или Ч:ММ
    m = re.search(r'(\d{1,2})[:.](\d{2})', raw)
    if not m:
        await update.message.reply_text(
            "Не нашёл время.\n"
            "Напиши так: /daily 07:00 утренняя медитация"
        )
        return

    hour = int(m.group(1))
    minute = int(m.group(2))

    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        await update.message.reply_text(
            "Странное время 😅\n"
            "Попробуй что-то вроде 06:30 или 21:05."
        )
        return

    # Всё после времени — это текст напоминания
    text = raw[m.end():].strip()
    if not text:
        text = "твоя ежедневная практика"

    chat_key = str(chat_id)

    item = {
        "hour": hour,
        "minute": minute,
        "text": text,
    }
    # имя для job, чтобы потом можно было отменить
    item["job_name"] = f"daily-{chat_id}-{hour:02d}{minute:02d}-{abs(hash(text))%10000}"

    daily_reminders.setdefault(chat_key, []).append(item)
    save_daily_reminders()

    # Создаём ежедневный job
    context.application.job_queue.run_daily(
        daily_job_callback,
        time=time(hour=hour, minute=minute),
        chat_id=chat_id,
        name=item["job_name"],
        data={"text": text},
    )

    await update.message.reply_text(
        f"🕰 Ежедневное напоминание создано:\n"
        f"каждый день в {hour:02d}:{minute:02d} — {text}"
    )

async def list_reminders_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать все напоминания для текущего чата (ежедневные + отложенные)."""
    chat_id = update.effective_chat.id
    chat_key = str(chat_id)

    daily_items = daily_reminders.get(chat_key, [])
    at_items = at_reminders.get(chat_key, [])

    if not daily_items and not at_items:
        await update.message.reply_text("У тебя пока нет активных напоминаний 🌱")
        return

    lines = []
    idx = 1

    # Сначала ежедневные
    for item in daily_items:
        lines.append(
            f"{idx}. 🔁 каждый день в {item['hour']:02d}:{item['minute']:02d} — {item.get('text', '')}"
        )
        idx += 1

    # Потом отложенные на дату
    for item in at_items:
        lines.append(
            f"{idx}. 📅 {item['date']} в {item['time']} — {item.get('text', '')}"
        )
        idx += 1

    await update.message.reply_text(
        "Твои напоминания:\n" + "\n".join(lines)
    )

async def cancel_reminder_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отменить одно из напоминаний по номеру (ежедневное или отложенное)."""
    chat_id = update.effective_chat.id
    chat_key = str(chat_id)

    daily_items = daily_reminders.get(chat_key, [])
    at_items = at_reminders.get(chat_key, [])

    total = len(daily_items) + len(at_items)

    if total == 0:
        await update.message.reply_text("Отменять нечего — напоминаний нет.")
        return

    if not context.args:
        await update.message.reply_text(
            "Напиши номер напоминания, которое хочешь отменить.\n"
            "Например: /cancel_reminder 1\n"
            "Список можно посмотреть через /reminders"
        )
        return

    try:
        idx = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Нужен номер, например: /cancel_reminder 1")
        return

    if idx < 1 or idx > total:
        await update.message.reply_text(
            "Такого номера нет.\n"
            "Посмотри список через /reminders"
        )
        return

    # Сначала идут ежедневные
    if idx <= len(daily_items):
        item = daily_items.pop(idx - 1)
        save_daily_reminders()

        job_name = item.get("job_name")
        if job_name:
            for job in context.application.job_queue.get_jobs_by_name(job_name):
                job.schedule_removal()

        await update.message.reply_text(
            f"❌ Ежедневное напоминание отменено:\n"
            f"каждый день в {item['hour']:02d}:{item['minute']:02d} — {item.get('text', '')}"
        )
        return
    # Если индекс больше — значит, это отложенное напоминание
    at_index = idx - len(daily_items) - 1
    item = at_items.pop(at_index)
    save_at_reminders()

    # Останавливаем связанные jobs (за час и в сам момент)
    for job_name in (item.get("pre_job_name"), item.get("main_job_name")):
        if job_name:
            for job in context.application.job_queue.get_jobs_by_name(job_name):
                job.schedule_removal()

    await update.message.reply_text(
        f"❌ Отложенное напоминание отменено:\n"
        f"{item['date']} в {item['time']} — {item.get('text', '')}"
    )

# === Избранное ===
async def fav_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /fav — сохранить в избранное.

    Варианты:
    1) Ответом на сообщение: /fav  -> сохраняем текст того сообщения
    2) /fav какой-то текст         -> сохраняем переданный текст
    3) Просто /fav                 -> сохраняем последний ответ бота в этом чате
    """
    chat_id = update.effective_chat.id
    chat_key = str(chat_id)

    source_text = None

    # 1) Если команда в ответ на сообщение
    if update.message.reply_to_message and update.message.reply_to_message.text:
        source_text = update.message.reply_to_message.text.strip()

    # 2) Если есть аргументы: /fav текст...
    elif context.args:
        source_text = " ".join(context.args).strip()

    # 3) Иначе — берём последний ответ бота
    else:
        source_text = last_bot_messages.get(chat_id)

    if not source_text:
        await update.message.reply_text(
            "Не нашёл, что сохранить в избранное 🤔\n"
            "Сделай /fav ответом на нужное сообщение или просто напиши /fav после моего ответа."
        )
        return

    entry = {
        "text": source_text,
        "created": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    favorites.setdefault(chat_key, []).append(entry)
    save_favorites()

    idx = len(favorites[chat_key])
    await update.message.reply_text(f"⭐ Сохранил в избранное под номером {idx}.")


async def favorites_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /favorites — показать список избранного.
    """
    chat_id = update.effective_chat.id
    chat_key = str(chat_id)

    items = favorites.get(chat_key, [])
    if not items:
        await update.message.reply_text("Пока в избранном пусто 🌱\n"
                                        "Можешь сохранить что-то через /fav.")
        return

    lines = ["Твоё избранное:"]
    for i, item in enumerate(items, start=1):
        text = item.get("text", "")
        created = item.get("created", "")
        preview = text.replace("\n", " ")
        if len(preview) > 80:
            preview = preview[:77] + "…"
        lines.append(f"{i}. ({created}) {preview}")

    lines.append(
        "\nКоманды:\n"
        "• /fav — сохранить в избранное (ответом на сообщение или после моего ответа)\n"
        "• /favorites — список избранного\n"
        "• /fav_show N — показать пункт полностью\n"
        "• /fav_del N — удалить пункт"
    )

    await update.message.reply_text("\n".join(lines))

async def fav_del_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /fav_del N — удалить пункт N из избранного.
    """
    chat_id = update.effective_chat.id
    chat_key = str(chat_id)

    items = favorites.get(chat_key, [])
    if not items:
        await update.message.reply_text("В избранном пока ничего нет 🌱")
        return

    if not context.args:
        await update.message.reply_text(
            "Укажи номер, который хочешь удалить.\n"
            "Например: /fav_del 2\n"
            "Список можно посмотреть через /favorites."
        )
        return

    try:
        idx = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Нужен номер, например: /fav_del 1")
        return

    if idx < 1 or idx > len(items):
        await update.message.reply_text("Такого номера в избранном нет.")
        return

    removed = items.pop(idx - 1)
    save_favorites()

    preview = removed.get("text", "").replace("\n", " ")
    if len(preview) > 80:
        preview = preview[:77] + "…"

    await update.message.reply_text(
        f"🗑 Удалил из избранного:\n{preview}"
    )
async def fav_show_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /fav_show N  или  /favshow N
    Показывает полный текст пункта избранного под номером N.
    """
    chat_id = update.effective_chat.id
    chat_key = str(chat_id)

    items = favorites.get(chat_key, [])
    if not items:
        await update.message.reply_text("В избранном пока ничего нет 🌱")
        return

    if not context.args:
        await update.message.reply_text(
            "Укажи номер, который хочешь открыть полностью.\n"
            "Например: /fav_show 2\n"
            "Список можно посмотреть через /favorites."
        )
        return

    try:
        idx = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Нужен номер, например: /fav_show 1")
        return

    if idx < 1 or idx > len(items):
        await update.message.reply_text("Такого номера в избранном нет.")
        return

    item = items[idx - 1]
    text_full = item.get("text", "")
    created = item.get("created", "")

    if not text_full:
        await update.message.reply_text("Этот пункт избранного пустой.")
        return

    header = f"⭐ Избранное #{idx} ({created})\n\n"
    message = header + text_full

    # Телеграм ограничивает длину сообщения ~4096 символов, осторожно делим.
    MAX_LEN = 4000

    if len(message) <= MAX_LEN:
        await update.message.reply_text(message)
    else:
        # отправляем заголовок отдельно
        await update.message.reply_text(header)
        # режем текст на части
        for i in range(0, len(text_full), MAX_LEN):
            chunk = text_full[i:i+MAX_LEN]
            await update.message.reply_text(chunk)


async def handle_favorites_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Кнопка ⭐ Избранное — просто показывает список (как /favorites).
    """
    await favorites_command(update, context)

async def set_timezone_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /set_timezone Europe/Moscow
    /set_timezone Москва
    /set_timezone Прага
    """
    chat_id = update.effective_chat.id

    if not context.args:
        await update.message.reply_text(
            "Напиши, в какой таймзоне ты живёшь.\n"
            "Примеры:\n"
            "/set_timezone Europe/Prague\n"
            "/set_timezone Europe/Moscow\n"
            "/set_timezone Москва\n"
            "/set_timezone Прага"
        )
        return

    raw = " ".join(context.args)
    tz_name = normalize_tz_name(raw)

    # Проверяем, что такая таймзона реально существует
    try:
        ZoneInfo(tz_name)
    except Exception:
        await update.message.reply_text(
            "Не смог понять таймзону 😔\n"
            "Попробуй в формате IANA, например:\n"
            "Europe/Prague, Europe/Moscow, Europe/Minsk, Europe/Kiev, Asia/Almaty."
        )
        return

    user_timezones[str(chat_id)] = tz_name
    save_timezones()

    await update.message.reply_text(
        f"🕰 Таймзона сохранена: *{tz_name}*.\n"
        f"Новые напоминания будут идти по этому местному времени.",
        parse_mode="Markdown",
    )
async def dialog_default_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await switch_dialog(update, context, "default")

async def dialog_soul_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await switch_dialog(update, context, "soul")

async def dialog_yoga_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await switch_dialog(update, context, "yoga")

async def dialog_tech_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await switch_dialog(update, context, "tech")

async def dialog_music_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await switch_dialog(update, context, "music")

async def set_dialog_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /dialog — показать текущий диалог и список доступных
    /dialog soul
    /dialog yoga
    /dialog tech
    /dialog music
    """
    if not context.args:
        current = get_active_dialog_title(context)
        available = "\n".join(
            f"- {code}: {title}"
            for code, title in DIALOG_TITLES.items()
        )
        await update.message.reply_text(
            "🧩 Сейчас мы говорим в диалоге:\n"
            f"{current}\n\n"
            "Чтобы переключиться, напиши, например:\n"
            "/dialog soul\n"
            "/dialog yoga\n"
            "/dialog tech\n"
            "/dialog music\n\n"
            "Доступные диалоги:\n" + available
        )
        return

    key = context.args[0].strip().lower()

    aliases = {
        "душа": "soul",
        "soul": "soul",

        "йога": "yoga",
        "yoga": "yoga",

        "техника": "tech",
        "тех": "tech",
        "tech": "tech",

        "музыка": "music",
        "music": "music",

        "общий": "default",
        "main": "default",
        "default": "default",
    }

    dlg_id = aliases.get(key, key)
    await switch_dialog(update, context, dlg_id)

async def switch_dialog(update: Update, context: ContextTypes.DEFAULT_TYPE, dlg_id: str):
    """Внутренняя функция для переключения диалога (и через /dialog, и через кнопки)."""
    if dlg_id not in DIALOG_TITLES:
        available = ", ".join(DIALOG_TITLES.keys())
        await update.message.reply_text(
            "Я не знаю такого диалога.\n"
            f"Попробуй один из: {available}"
        )
        return

    context.user_data["dialog_id"] = dlg_id
    title = DIALOG_TITLES[dlg_id]

    await update.message.reply_text(
        f"✨ Теперь мы продолжаем диалог: {title}\n"
        "Все новые сообщения будут относиться к этой линии."
    )

# Кнопки:
async def dialog_default_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await switch_dialog(update, context, "default")


async def dialog_soul_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await switch_dialog(update, context, "soul")


async def dialog_yoga_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await switch_dialog(update, context, "yoga")


async def dialog_tech_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await switch_dialog(update, context, "tech")


async def dialog_music_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await switch_dialog(update, context, "music")

# ================================
# ⏰ ВСПОМОГАТЕЛЬНАЯ ФУНКЦИЯ ДЛЯ ВРЕМЕНИ
# ================================
def parse_time_str(time_str):
    """
    Парсит строку времени вида '7:15', '07:15', '7.15', '07.15'
    Возвращает (hour, minute) или None, если не получилось.
    """
    if not time_str:
        return None

    s = time_str.strip()
    # убираем пробелы и приводим к формату HH:MM
    s = s.replace(" ", "")
    s = s.replace(".", ":")

    m = re.match(r"^(\d{1,2}):(\d{1,2})$", s)
    if not m:
        return None

    hour = int(m.group(1))
    minute = int(m.group(2))

    # простая проверка диапазонов
    if not (0 <= hour < 24 and 0 <= minute < 60):
        return None

    return hour, minute


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

# для распознавания времени вида 7:00, 07:00, 21:30
TIME_PATTERN = re.compile(r"(\d{1,2}:\d{2})")

# ===== Handlers =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        ["⭐ Избранное", "⏰ Создать напоминание"],
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text(
        "Напиши мне сообщение или выбери кнопку 👇",
        reply_markup=reply_markup,
    )

# === 🌸 Мантра дня ===
async def handle_mantra(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    lines = load_lines("mantras.txt")
    reply = random.choice(lines) if lines else "⚠️ Файл с мантрами пуст."
    await update.message.reply_text(reply)
    remember_bot_message(update.effective_chat.id, reply)
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
    remember_bot_message(update.effective_chat.id, reply)
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
    remember_bot_message(update.effective_chat.id, reply)
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
    remember_bot_message(update.effective_chat.id, reply)
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
        transcript = client.audio.transcriptions.create(
            model="gpt-4o-mini-transcribe",
            file=f
        )
    text = transcript.text or "(пусто)"
    save_message(chat_id, "user", f"[voice] {text}")

    await context.bot.send_message(
        chat_id=GURU_CHAT_ID,
        text=f"🎙️ Голос от {user.first_name} (@{user.username}):\n{text}\n(chat_id: {chat_id})"
    )

    system_message = {"role": "system", "content": load_personality() + "\n\n" + load_knowledge()}
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[system_message, {"role": "user", "content": text}]
    )
    bot_reply = response.choices[0].message.content
    save_message(chat_id, "assistant", bot_reply)

    await update.message.reply_text(bot_reply)
    remember_bot_message(update.effective_chat.id, bot_reply)
    
    try:
        speech_file = f"reply_{chat_id}.mp3"
        tts_cartesia_to_file(bot_reply, speech_file)
        with open(speech_file, "rb") as vf:
            await context.bot.send_voice(chat_id=update.effective_chat.id, voice=vf)
    except Exception as e:
        logging.warning(f"TTS error: {e}")

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

    if chat_type != "private":
        return

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

    await context.bot.send_message(
        chat_id=GURU_CHAT_ID,
        text=(
            f"🖼 Фото от {user.first_name} (@{user.username}), chat_id {chat_id}.\n"
            f"Ответ бота:\n{reply}\n\n➡️ /reply {chat_id} твой_текст"
        )
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
        img_resp = client.images.generate(
            model="gpt-image-1",
            prompt=prompt_text,
            size="1024x1024",
        )
        b64 = img_resp.data[0].b64_json
        img_bytes = base64.b64decode(b64)
        out_name = f"draw_{int(datetime.now().timestamp())}.png"
        with open(out_name, "wb") as f:
            f.write(img_bytes)
        with open(out_name, "rb") as pic:
            await context.bot.send_photo(
                chat_id=update.effective_chat.id,
                photo=pic,
                caption="🖼 Готово.",
            )

        save_message(chat_id, "assistant", f"[image_generated] {prompt_text}")

        await context.bot.send_message(
            chat_id=GURU_CHAT_ID,
            text=(
                f"🖼 Изображение создано для ({chat_id}).\n"
                f"Текст: {prompt_text}\n\n➡️ /reply {chat_id} твой_текст"
            )
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


# === Кнопка "⏰ Создать напоминание" ===
async def handle_reminders_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    text = (
        "🕰 *Напоминания — как пользоваться*\n\n"
        "1️⃣ *Одноразовое напоминание на сегодня*\n"
        "   Пример:\n"
        "   `/remind_once 07:00 утренняя медитация`\n"
        "   (сработает один раз в ближайший 07:00)\n\n"
        "2️⃣ *Ежедневное напоминание*\n"
        "   Пример:\n"
        "   `/daily 07:00 утренняя медитация`\n"
        "   (будет приходить каждый день в 07:00)\n\n"
        "3️⃣ *Посмотреть все мои ежедневные напоминания*\n"
        "   Команда:\n"
        "   `/reminders`\n\n"
        "4️⃣ *Удалить ежедневное напоминание по номеру*\n"
        "   Пример:\n"
        "   `/cancel_reminder 1`\n"
        "   (номер берётся из списка `/reminders`)\n\n"
        "5️⃣ *Отложенное напоминание на дату и время*\n"
        "   Пример:\n"
        "   `/remind_at 25.12 12:00 семейный созвон`\n"
        "   (я напомню за час и в момент события)\n\n"
        "➕ Также работает простая фраза:\n"
        "   `напомни мне в 07:00 утренняя медитация`\n"
        "   — это одноразовое напоминание на ближайшее такое время.\n"
    )

    await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode="Markdown"
    )


# === GPT чат ===
async def chat_with_gpt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = str(update.effective_chat.id)
    user_message = update.message.text or ""

    # режим простого напоминания после кнопки
    if context.user_data.get("awaiting_simple_reminder"):
        context.user_data["awaiting_simple_reminder"] = False

        parts = (user_message or "").strip().split(maxsplit=1)
        if len(parts) < 2:
            await update.message.reply_text(
                "Мне нужно время и текст в одном сообщении.\n"
                "Например:\n07:30 выпей воды"
            )
            return

        time_str, note_text = parts[0], parts[1].strip()
        parsed = parse_time_str(time_str)
        if not parsed:
            await update.message.reply_text(
                "Я не понял время. Нужен формат HH:MM, например 07:30."
            )
            return

        hour, minute = parsed

        tz = get_user_timezone(update.effective_chat.id)
        now = datetime.now(tz)
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)

        delay = (target - now).total_seconds()
        when_str = target.strftime("%Y-%m-%d %H:%M")
        real_chat_id = update.effective_chat.id

        async def one_shot():
            try:
                await asyncio.sleep(delay)
                await context.bot.send_message(
                    chat_id=real_chat_id,
                    text=f"🔔 Напоминание:\n{note_text}"
                )
            except Exception as e:
                logging.warning(f"Simple reminder error: {e}")

        task = asyncio.create_task(one_shot())
        reminder_tasks.setdefault(real_chat_id, []).append(task)

        await update.message.reply_text(
            f"✅ Напоминание запланировано на {when_str}.\nТекст: {note_text}"
        )
        return

    # естественное "напомни мне в 16:30 выпей воды"
    lower = (user_message or "").lower()
    time_match = TIME_PATTERN.search(user_message or "")

    if "напомни" in lower and time_match:
        time_str = time_match.group(1)
        parsed = parse_time_str(time_str)

        if not parsed:
            await update.message.reply_text(
                "Я увидел слово \"напомни\", но не смог разобрать время.\n"
                "Напиши, например: напомни мне в 07:00 выпей воды."
            )
            return

        hour, minute = parsed
        note_text = (user_message[time_match.end():] or "").strip(" ,.-\n")
        if not note_text:
            note_text = "напоминание"

        tz = get_user_timezone(update.effective_chat.id)
        now = datetime.now(tz)
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)

        delay = (target - now).total_seconds()
        when_str = target.strftime("%Y-%m-%d %H:%M")
        real_chat_id = update.effective_chat.id

        async def one_shot_natural():
            try:
                await asyncio.sleep(delay)
                await context.bot.send_message(
                    chat_id=real_chat_id,
                    text=f"🔔 Напоминание:\n{note_text}"
                )
            except Exception as e:
                logging.warning(f"Natural reminder error: {e}")

        task = asyncio.create_task(one_shot_natural())
        reminder_tasks.setdefault(real_chat_id, []).append(task)

        await update.message.reply_text(
            f"✅ Я понял: ты хочешь напоминание.\n"
            f"Запланировано на {when_str}.\nТекст: {note_text}"
        )
        return

    # картинка после кнопки
    if context.user_data.get("awaiting_draw_prompt") and user_message and not user_message.startswith("/"):
        context.user_data["awaiting_draw_prompt"] = False
        await _generate_and_send_image(update, context, user_message)
        return

    # авто-рисование по тексту
    if wants_image(user_message):
        prompt = extract_prompt(user_message)
        if not prompt or len(prompt) < 4:
            await update.message.reply_text(
                "🎨 Опиши чуть подробнее, что рисовать (стиль/цвета/атмосфера)."
            )
            return
        await _generate_and_send_image(update, context, prompt)
        return

    # --- МНОГОДИАЛОГОВАЯ ИСТОРИЯ ---

    dialog_id = get_active_dialog_id(context)

    # структура: chat_histories[chat_id][dialog_id] = [messages...]
    dialog_map = chat_histories.setdefault(chat_id, {})
    dialog_history = dialog_map.setdefault(dialog_id, [])

    dialog_history.append({"role": "user", "content": user_message})
    save_message(chat_id, "user", user_message, dialog_id=dialog_id)

    forward_text = (
        f"❓ Вопрос от {user.first_name} (@{user.username}):\n"
        f"{user_message}\n"
        f"(chat_id: {chat_id}, dialog: {dialog_id})"
    )
    await context.bot.send_message(chat_id=GURU_CHAT_ID, text=forward_text)

    system_message = {
        "role": "system",
        "content": load_personality() + "\n\n" + load_knowledge(),
    }

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[system_message] + dialog_history,
    )
    bot_reply = response.choices[0].message.content

    dialog_history.append({"role": "assistant", "content": bot_reply})
    save_message(chat_id, "assistant", bot_reply, dialog_id=dialog_id)


    await update.message.reply_text(bot_reply)
    remember_bot_message(update.effective_chat.id, bot_reply)
    
    try:
        speech_file = f"reply_{chat_id}.mp3"
        tts_cartesia_to_file(bot_reply, speech_file)
        with open(speech_file, "rb") as vf:
            await context.bot.send_voice(chat_id=update.effective_chat.id, voice=vf)
    except Exception as e:
        logging.warning(f"TTS error: {e}")

    await context.bot.send_message(
        chat_id=GURU_CHAT_ID,
        text=f"🤖 Ответ бота ученику ({chat_id}):\n{bot_reply}\n\n➡️ /reply {chat_id} твой_текст"
    )

# === Одноразовое напоминание ===
async def remind_once(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    if len(context.args) < 2:
        await update.message.reply_text(
            "Формат: /remind_once HH:MM текст\n\nНапример:\n"
            "/remind_once 06:00 медитация Ом Намах Шивая"
        )
        return

    time_str = context.args[0]
    note_text = " ".join(context.args[1:]).strip()

    parsed = parse_time_str(time_str)
    if not parsed:
        await update.message.reply_text(
            "Не понял время. Используй формат HH:MM, например 07:15."
        )
        return

    hour, minute = parsed
    tz = get_user_timezone(chat_id)
    now = datetime.now(tz)
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)

    delay = (target - now).total_seconds()
    when_str = target.strftime("%Y-%m-%d %H:%M")

    async def one_shot():
        try:
            await asyncio.sleep(delay)
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"🔔 Напоминание:\n{note_text}"
            )
        except Exception as e:
            logging.warning(f"Reminder once error: {e}")

    task = asyncio.create_task(one_shot())
    reminder_tasks.setdefault(chat_id, []).append(task)

    await update.message.reply_text(
        f"✅ Одноразовое напоминание запланировано на {when_str}.\n"
        f"Текст: {note_text}"
    )
# === Отложенное напоминание на дату и время (с предупреждением за час) ===
async def remind_at(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    if len(context.args) < 3:
        await update.message.reply_text(
            "Формат:\n"
            "/remind_at DD.MM HH:MM текст\n\n"
            "Например:\n"
            "/remind_at 25.12 12:00 семейный созвон"
        )
        return

    date_str = context.args[0]      # 25.12
    time_str = context.args[1]      # 12:00
    note_text = " ".join(context.args[2:]).strip()

    # Парсим дату
    try:
        day, month = map(int, date_str.split("."))
    except Exception:
        await update.message.reply_text(
            "Не понял дату. Нужен формат ДД.ММ, например 25.12"
        )
        return

    # Парсим время
    parsed_time = parse_time_str(time_str)
    if not parsed_time:
        await update.message.reply_text(
            "Не понял время. Нужен формат ЧЧ:ММ, например 12:00"
        )
        return

    hour, minute = parsed_time

    tz = get_user_timezone(chat_id)
    now = datetime.now(tz)

    # Год берём текущий
    try:
        target = datetime(
            year=now.year,
            month=month,
            day=day,
            hour=hour,
            minute=minute,
            second=0,
            microsecond=0,
            tzinfo=tz,
        )
    except Exception:
        await update.message.reply_text(
            "Похоже, странная дата (в календаре её нет)."
        )
        return

    # Если дата/время уже в прошлом — переносим на следующий год
    if target <= now:
        target = target.replace(year=now.year + 1)

    main_delay = (target - now).total_seconds()
    pre_time = target - timedelta(hours=1)
    pre_delay = max((pre_time - now).total_seconds(), 0)

    job_queue = context.application.job_queue

    ts_key = int(target.timestamp())
    pre_job_name = f"at-pre-{chat_id}-{ts_key}"
    main_job_name = f"at-main-{chat_id}-{ts_key}"

    # Запускаем jobs
    job_queue.run_once(
        at_pre_job,
        when=pre_delay,
        chat_id=chat_id,
        name=pre_job_name,
        data={"text": note_text},
    )
    job_queue.run_once(
        at_main_job,
        when=main_delay,
        chat_id=chat_id,
        name=main_job_name,
        data={"text": note_text},
    )

    # сохраняем в список отложенных
    chat_key = str(chat_id)
    at_entry = {
        "date": target.strftime("%d.%m.%Y"),
        "time": target.strftime("%H:%M"),
        "text": note_text,
        "pre_job_name": pre_job_name,
        "main_job_name": main_job_name,
    }
    at_reminders.setdefault(chat_key, []).append(at_entry)
    save_at_reminders()

    await update.message.reply_text(
        f"✅ Отложенное напоминание создано.\n"
        f"Дата: {target.strftime('%d.%m.%Y')}\n"
        f"Время: {time_str}\n"
        f"Текст: {note_text}\n"
        f"Также напомню за час — в {pre_time.strftime('%H:%M')}."
    )
# === Ежедневное напоминание ===
async def remind_daily(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    if len(context.args) < 2:
        await update.message.reply_text(
            "Формат: /remind_daily HH:MM текст\n\nНапример:\n"
            "/remind_daily 21:30 вечерняя медитация"
        )
        return

    time_str = context.args[0]
    note_text = " ".join(context.args[1:]).strip()

    parsed = parse_time_str(time_str)
    if not parsed:
        await update.message.reply_text(
            "Не понял время. Используй формат HH:MM, например 09:00."
        )
        return

    hour, minute = parsed
    tz = get_user_timezone(chat_id)
    now = datetime.now(tz)
    first = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if first <= now:
        first += timedelta(days=1)

    first_delay = (first - now).total_seconds()
    time_pretty = f"{hour:02d}:{minute:02d}"

    async def daily_loop():
        try:
            await asyncio.sleep(first_delay)
            while True:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"🔔 Ежедневное напоминание ({time_pretty}):\n{note_text}"
                )
                await asyncio.sleep(24 * 60 * 60)
        except asyncio.CancelledError:
            logging.info(f"Daily reminder for chat {chat_id} cancelled")
        except Exception as e:
            logging.warning(f"Reminder daily error: {e}")

    task = asyncio.create_task(daily_loop())
    reminder_tasks.setdefault(chat_id, []).append(task)

    await update.message.reply_text(
        f"✅ Ежедневное напоминание установлено на {time_pretty}.\n"
        f"Текст: {note_text}"
    )


# === Отключить все напоминания ===
async def remind_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    tasks = reminder_tasks.pop(chat_id, [])

    for t in tasks:
        t.cancel()

    if tasks:
        await update.message.reply_text(
            "⏹ Все активные напоминания для этого чата остановлены."
        )
    else:
        await update.message.reply_text("У тебя не было активных напоминаний.")


# --- Групповой чат ---
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
    await update.message.reply_text(
        f"{user.first_name}, {bot_reply}",
        reply_to_message_id=update.message.message_id
    )

    await context.bot.send_message(
        chat_id=GURU_CHAT_ID,
        text=f"🤖 Ответ бота в группе ({chat_id}):\n{bot_reply}\n\n➡️ /reply {chat_id} твой_текст"
    )


# --- Ответ Гуру ---
async def reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Только из Guru-чата
    if update.effective_chat.id != GURU_CHAT_ID:
        await update.message.reply_text("Эта команда доступна только Гуру.")
        return

    # Берём текст из подписи к фото или из обычного сообщения
    caption_text = (update.message.caption or update.message.text or "").strip()
    if not caption_text:
        await update.message.reply_text("⚠️ Использование: /reply CHAT_ID [текст]")
        return

    # Разбираем максимум на три части: /reply, CHAT_ID, остальной текст
    parts = caption_text.split(maxsplit=2)

    # Ожидаем:
    #  - "/reply 123456 текст..."
    #  - "/reply 123456"
    #  - "123456 текст..." (если когда-нибудь захочешь без слова /reply)
    if parts[0] == "/reply":
        if len(parts) < 2:
            await update.message.reply_text("⚠️ Использование: /reply CHAT_ID [текст]")
            return
        target_chat_id = parts[1]
        reply_text = parts[2] if len(parts) >= 3 else ""
    else:
        if len(parts) < 1:
            await update.message.reply_text("⚠️ Использование: /reply CHAT_ID [текст]")
            return
        target_chat_id = parts[0]
        reply_text = parts[1] if len(parts) >= 2 else ""

    target_chat_id = target_chat_id.strip()
    reply_text = reply_text.strip()

    # --- Если есть фото ---
    if update.message.photo:
        photo = update.message.photo[-1]  # самое большое по размеру
        caption = reply_text if reply_text else "📩 Трансцендентное изображение"

        try:
            if len(caption) > 1024:
                short_caption = caption[:1000] + "…"
                await context.bot.send_photo(
                    chat_id=target_chat_id,
                    photo=photo.file_id,
                    caption=short_caption,
                )
                await context.bot.send_message(
                    chat_id=target_chat_id,
                    text=caption,
                )
            else:
                await context.bot.send_photo(
                    chat_id=target_chat_id,
                    photo=photo.file_id,
                    caption=caption,
                )

            await update.message.reply_text("✅ Фото и послание отправлены ученику.")
        except Exception as e:
            await update.message.reply_text(
                f"⚠️ Ошибка при отправке фото ученику:\n{e}"
            )
        return

    # --- Если без фото — обычный текст ---
    try:
        await context.bot.send_message(
            chat_id=target_chat_id,
            text=f"📩 Трансцендентный ответ:\n{reply_text}"
            if reply_text
            else "📩 Трансцендентное послание."
        )
        await update.message.reply_text("✅ Ответ отправлен ученику.")
    except Exception as e:
        await update.message.reply_text(
            f"⚠️ Ошибка при отправке текста ученику:\n{e}"
        )

# ===== Main =====
def main():
    app = (
        ApplicationBuilder()
        .token(TELEGRAM_TOKEN)
        .post_init(start_reminders)   # колбэк, который поднимает напоминания и ставит команды
        .build()
    )

    # Таймзона для JobQueue
    app.job_queue.scheduler.timezone = ZoneInfo("Europe/Prague")

    # Восстанавливаем ежедневные и «на дату» напоминания
    restore_daily_jobs(app)
    restore_at_jobs(app)

    # ==== COMMAND HANDLERS ====
    app.add_handler(CommandHandler("start", start))

    app.add_handler(CommandHandler("mantra", handle_mantra))
    app.add_handler(CommandHandler("advice", handle_advice))
    app.add_handler(CommandHandler("story", handle_story))
    app.add_handler(CommandHandler("song", handle_song))
    app.add_handler(CommandHandler("draw", draw))

    # Напоминания
    app.add_handler(CommandHandler("remind_once", remind_once))
    app.add_handler(CommandHandler("daily", daily_reminder_command))   # /daily 07:00 текст
    app.add_handler(CommandHandler("remind_daily", remind_daily))      # /remind_daily 07:00 текст (если хочешь оставить)
    app.add_handler(CommandHandler("remind_at", remind_at))
    app.add_handler(CommandHandler("reminders", list_reminders_command))
    app.add_handler(CommandHandler("cancel_reminder", cancel_reminder_command))

    # Избранное
    app.add_handler(CommandHandler("fav", fav_command))
    app.add_handler(CommandHandler("favorites", favorites_command))
    app.add_handler(CommandHandler("fav_show", fav_show_command))
    app.add_handler(CommandHandler("fav_del", fav_del_command))

    # Таймзона и диалоги
    app.add_handler(CommandHandler("set_timezone", set_timezone_command))
    app.add_handler(CommandHandler("dialog", set_dialog_command))
    app.add_handler(CommandHandler("dialog_default", dialog_default_command))
    app.add_handler(CommandHandler("dialog_soul",    dialog_soul_command))
    app.add_handler(CommandHandler("dialog_yoga",    dialog_yoga_command))
    app.add_handler(CommandHandler("dialog_tech",    dialog_tech_command))
    app.add_handler(CommandHandler("dialog_music",   dialog_music_command))

    # Ответ Гуру
    # app.add_handler(CommandHandler("reply", reply))
    # Ответ Гуру — текстовая команда /reply в Guru-чате
    app.add_handler(
        MessageHandler(
            filters.Chat(GURU_CHAT_ID) & filters.TEXT & filters.Regex(r"^/reply\b"),
            reply,
        )
    )

    # Ответ Гуру — фото с подписью, начинающейся с /reply, в Guru-чате
    app.add_handler(
        MessageHandler(
            filters.Chat(GURU_CHAT_ID) & filters.PHOTO & filters.CaptionRegex(r"^/reply\b"),
            reply,
        )
    )

    # ==== MESSAGE HANDLERS ====

    # Голосовые
    app.add_handler(MessageHandler(filters.VOICE & ~filters.COMMAND, handle_voice))

    # Фото
    app.add_handler(MessageHandler(filters.PHOTO & ~filters.COMMAND, handle_photo))

    # Кнопки нижней клавиатуры
    app.add_handler(MessageHandler(filters.Regex("^⭐ Избранное$"), handle_favorites_button))
    app.add_handler(MessageHandler(filters.Regex("^⏰ Создать напоминание$"), handle_reminders_button))

    # Группы — ответы по @username
    app.add_handler(MessageHandler(filters.TEXT & filters.ChatType.GROUPS, group_chat))

    # Всё остальное текстовое в личке — в GPT-диалог
    app.add_handler(MessageHandler(filters.TEXT & filters.ChatType.PRIVATE, chat_with_gpt))

    logging.info("🤖 Бот запущен и принимает команды.")
    print("🤖 Бот запущен и принимает команды.")

    app.run_polling()

if __name__ == "__main__":
    main()
