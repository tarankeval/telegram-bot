import json
import glob
import os

def read_all_histories():
    # Ищем все файлы history_*.json
    files = sorted(glob.glob("history_*.json"))

    if not files:
        print("❌ Историй пока нет.")
        return

    for file_path in files:
        print("="*60)
        print(f"📖 Файл: {os.path.basename(file_path)}")
        print("="*60)

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError:
            print("⚠️ Ошибка чтения файла:", file_path)
            continue

        # Перебор чатов в файле
        for chat_id, messages in data.items():
            print(f"\n💬 Чат ID: {chat_id}\n")
            for msg in messages:
                role = "👤 Пользователь" if msg["role"] == "user" else "🤖 Бот"
                print(f"{role}: {msg['content']}\n")

if __name__ == "__main__":
    read_all_histories()

