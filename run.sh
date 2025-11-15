#!/bin/bash

# Путь к проекту
BOT_DIR="$HOME/telegram-bot"
BOT_FILE="bot.py"
PID_FILE="$BOT_DIR/bot.pid"

cd "$BOT_DIR" || exit 1

case "$1" in
  start)
    echo "🚀 Запуск бота..."
    source venv/bin/activate
    nohup python3 "$BOT_FILE" > bot.log 2>&1 &
    echo $! > "$PID_FILE"
    echo "Бот запущен, PID: $(cat $PID_FILE)"
    ;;
  stop)
    if [ -f "$PID_FILE" ]; then
      PID=$(cat "$PID_FILE")
      echo "🛑 Остановка бота (PID: $PID)..."
      kill "$PID" && rm -f "$PID_FILE"
      echo "Бот остановлен."
    else
      echo "❌ PID-файл не найден. Возможно, бот не запущен."
    fi
    ;;
  restart)
    $0 stop
    sleep 2
    $0 start
    ;;
  status)
    if [ -f "$PID_FILE" ]; then
      PID=$(cat "$PID_FILE")
      if ps -p "$PID" > /dev/null; then
        echo "✅ Бот работает (PID: $PID)"
      else
        echo "❌ PID-файл есть, но процесс не найден."
      fi
    else
      echo "ℹ️ Бот не запущен."
    fi
    ;;
  *)
    echo "Использование: ./run.sh {start|stop|restart|status}"
    exit 1
    ;;
esac
