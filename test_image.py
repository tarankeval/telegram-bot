from openai import OpenAI

client = OpenAI(api_key="(ВСТАВЬ СВОИ)")

try:
    result = client.images.generate(
        model="gpt-image-1",
        prompt="Красивый мистический лотос в сияющем космосе",
        size="512x512"
    )
    print("✅ Успех! Картинка создана.")
    print(result.data[0].url)
except Exception as e:
    print("⚠️ Ошибка:", e)
