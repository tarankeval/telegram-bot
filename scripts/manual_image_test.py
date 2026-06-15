"""Manual smoke test for OpenAI image generation."""

from openai import OpenAI

from config import load_settings


def main() -> None:
    settings = load_settings()
    client = OpenAI(api_key=settings.openai_api_key)
    result = client.images.generate(
        model="gpt-image-1",
        prompt="Красивый мистический лотос в сияющем космосе",
        size="1024x1024",
    )
    image = result.data[0]
    print(image.url or "Image generated successfully; response contains base64 data.")


if __name__ == "__main__":
    main()
