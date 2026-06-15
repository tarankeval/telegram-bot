# GuruChat Telegram Bot

Telegram-бот школы Hatha Yoga Lotus для текстового и голосового общения, практических советов, историй, мантр и генерации изображений.

Текущая версия: **GuruChat 2.0 "Kala Awakening"**.

## Возможности

- Диалог через OpenAI с настраиваемыми файлами `personality.txt` и `knowledge.txt`.
- Мантры, советы, истории и песни из текстовых файлов проекта.
- Распознавание голосовых сообщений.
- Необязательная озвучка ответов через Cartesia.
- Генерация изображений по `/draw` и естественным запросам.
- Ответы в группах при упоминании имени бота.
- Команда `/reply` для ответа пользователю из чата администратора.

## Требования

- Python 3.11 или новее.
- Telegram Bot Token от BotFather.
- OpenAI API key.
- Необязательно: Cartesia API key, model ID и voice ID.

## Установка

```bash
git clone https://github.com/tarankeval/telegram-bot.git
cd telegram-bot

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
```

Заполните `.env`:

```dotenv
TELEGRAM_TOKEN=...
OPENAI_API_KEY=...
GURU_CHAT_ID=642590466
HISTORY_DIR=.
```

Для голосовых ответов нужно заполнить все параметры Cartesia:

```dotenv
CARTESIA_API_KEY=...
CARTESIA_VERSION=...
CARTESIA_MODEL_ID=...
CARTESIA_VOICE_ID=...
```

Если Cartesia не настроена полностью, бот продолжит работать без озвучки ответов.

## Запуск

Обычный запуск:

```bash
source venv/bin/activate
python bot.py
```

Управление фоновым процессом:

```bash
chmod +x run.sh
./run.sh start
./run.sh status
./run.sh restart
./run.sh stop
```

## Docker

```bash
docker build -t guruchat-bot .
docker run --env-file .env --name guruchat-bot guruchat-bot
```

Для сохранения истории между перезапусками контейнера подключите каталог:

```bash
docker run --env-file .env -e HISTORY_DIR=/app/data -v "$PWD/data:/app/data" guruchat-bot
```

По умолчанию бот пишет `history_YYYY-MM-DD.json` в текущий рабочий каталог. Переменная `HISTORY_DIR` задаёт другой каталог.

## Systemd

Пример `telegram-bot.service` рассчитан на user service и каталог `~/telegram-bot`:

```bash
mkdir -p ~/.config/systemd/user
cp telegram-bot.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now telegram-bot
systemctl --user status telegram-bot
```

## Проверки

```bash
pip install -r requirements-dev.txt
ruff check .
python -m unittest discover -s tests -v
```

GitHub Actions выполняет эти проверки на Python 3.11 и 3.12.

## Структура

- `bot.py` — Telegram handlers и интеграции с внешними API.
- `config.py` — загрузка и проверка переменных окружения.
- `storage.py` — атомарная запись локальной истории.
- `helpers.py` — чистые функции распознавания команд и подготовки контекста.
- `personality.txt` — стиль и характер ответов.
- `knowledge.txt` — дополнительные знания бота.
- `mantras.txt`, `advices.txt`, `stories.txt`, `songs.txt` — контент кнопок.
- `scripts/manual_image_test.py` — ручная проверка OpenAI Images API.
- `tests/` — автоматические unit-тесты.

## Конфиденциальность

Это важно учитывать перед использованием:

- Текст, голосовые расшифровки и изображения отправляются внешним AI API для обработки.
- Вопросы пользователей и ответы бота пересылаются в чат `GURU_CHAT_ID`.
- История диалогов сохраняется локально в файлах `history_YYYY-MM-DD.json`.
- Эти файлы, `.env`, ключи, логи и временные медиа исключены из Git.

Перед публичным запуском сообщите пользователям об обработке данных и получите необходимое согласие согласно правилам вашей юрисдикции.

## Ручная проверка генерации изображений

```bash
python -m scripts.manual_image_test
```

Скрипт использует `OPENAI_API_KEY` из `.env` и не является частью автоматических тестов.
