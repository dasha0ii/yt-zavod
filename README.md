# TikTok → YouTube Bot

Telegram-бот на aiogram: скачивает TikTok-видео, транскрибирует аудио и публикует на YouTube.

## Стек

- `aiogram` — Telegram-бот
- `faster-whisper` — транскрибация
- `yt-dlp` — скачивание видео
- `Google API` — загрузка на YouTube
- `SQLite` — хранение данных

## Быстрый старт

```bash
git clone https://github.com/dasha0ii/yt-zavod.git
cd yt-zavod
cp .env.example .env
docker compose up --build
```

## Настройка

### `.env`

```
TELEGRAM_BOT_TOKEN=ваш_токен
```

Токен получить у [@BotFather](https://t.me/botfather).

### `client_secrets.json`

1. [Google Cloud Console](https://console.cloud.google.com/) → создать проект
2. Включить **YouTube Data API v3**
3. Создать OAuth 2.0 клиент (тип: веб-приложение)
4. URI перенаправления: `http://localhost:8080/`
5. Скачать JSON → переименовать в `client_secrets.json` → отправить содержимое в бота

## Структура

```
├── bot_aiogram.py       # основной файл бота
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── .env                 # создать из .env.example
├── output/              # обработанные видео
└── sources/             # исходники (фоны для видео)
```

## Остановка

```bash
docker compose down
```

## Локальная разработка

```bash
pip install -r requirements.txt
python bot_aiogram.py
```