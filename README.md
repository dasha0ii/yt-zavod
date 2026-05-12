# Telegram Bot: TikTok → Short Video → YouTube

Этот проект представляет собой Telegram-бота на базе aiogram, который автоматизирует процесс создания коротких видео из TikTok и их публикации на YouTube. Бот использует технологии машинного обучения для обработки аудио и видео контента.

## Оглавление

- [Установка Docker](#установка-docker)
- [Быстрый старт](#быстрый-старт)
- [Функционал бота](#функционал-бота)
- [Технологии](#технологии)
- [Запуск с помощью Docker](#запуск-с-помощью-docker)
  - [Предварительные требования](#предварительные-требования)
  - [Настройка переменных окружения](#настройка-переменных-окружения)
  - [Получение client_secrets.json](#получение-client_secretsjson)
  - [Шаги запуска](#шаги-запуска)
  - [Остановка бота](#остановка-бота)
- [Структура проекта](#структура-проекта)
- [Использование бота](#использование-бота)
- [Переменные окружения](#переменные-окружения)
- [Volumes](#volumes)
- [Разработка](#разработка)

## Установка Docker

<details>
<summary>Установка Docker на Ubuntu/Debian</summary>

### Ubuntu/Debian

1. **Обновите пакеты:**
   ```bash
   sudo apt update
   ```

2. **Установите необходимые пакеты:**
   ```bash
   sudo apt install apt-transport-https ca-certificates curl gnupg lsb-release
   ```

3. **Добавьте GPG ключ Docker:**
   ```bash
   curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg
   ```

4. **Добавьте репозиторий Docker:**
   ```bash
   echo "deb [arch=amd64 signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
   ```

5. **Установите Docker Engine:**
   ```bash
   sudo apt update
   sudo apt install docker-ce docker-ce-cli containerd.io
   ```

6. **Установите Docker Compose:**
   ```bash
   sudo apt install docker-compose-plugin
   ```

7. **Добавьте пользователя в группу docker (опционально):**
   ```bash
   sudo usermod -aG docker $USER
   ```
   После этого перезайдите в систему.

8. **Проверьте установку:**
   ```bash
   docker --version
   docker compose version
   ```

</details>

## Быстрый старт

Если у вас уже установлен Docker и Docker Compose, выполните одну команду:

```bash
git clone https://github.com/dasha0ii/yt-zavod.git && cd yt-zavod && cp .env.example .env && docker compose up --build
```

Затем настройте `.env` файл с вашими токенами и добавьте `client_secrets.json`.

## Функционал бота

- **Скачивание видео из TikTok**: Бот может скачивать видео по ссылкам из TikTok
- **Создание коротких видео**: Автоматическая обработка и монтаж коротких клипов
- **Транскрибация аудио**: Использование faster-whisper для распознавания речи в видео
- **Загрузка на YouTube**: Автоматическая публикация обработанных видео на YouTube
- **Управление аккаунтами**: Добавление и управление несколькими YouTube аккаунтами
- **База данных**: SQLite база для хранения информации о видео и аккаунтах

## Технологии

- **aiogram** - фреймворк для Telegram ботов
- **faster-whisper** - быстрая модель для транскрибации аудио
- **yt-dlp** - инструмент для скачивания видео
- **Google API** - для взаимодействия с YouTube
- **SQLite** - база данных

## Запуск с помощью Docker

### Предварительные требования

- Docker
- Docker Compose
- Файл `client_secrets.json` от Google API (для YouTube)
- Файл `.env` с токеном Telegram бота

### Настройка переменных окружения

1. **Создайте файл `.env` в корне проекта:**
   ```bash
   cp .env.example .env  # или создайте вручную
   ```

2. **Получите токен Telegram бота:**
   - Перейдите к [@BotFather](https://t.me/botfather) в Telegram
   - Создайте нового бота командой `/newbot`
   - Скопируйте полученный токен

3. **Заполните `.env` файл:**
   ```
   TELEGRAM_BOT_TOKEN=ваш_токен_здесь
   ```

### Получение client_secrets.json

Для работы с YouTube API необходимо получить файл `client_secrets.json`. Следуйте этим шагам:

1. **Перейдите в [Google Cloud Console](https://console.cloud.google.com/)**

2. **Создайте новый проект или выберите существующий:**
   - Нажмите на выпадающий список проектов в верхней панели
   - Выберите "Новый проект" или выберите существующий

3. **Включите YouTube Data API v3:**
   - В меню слева выберите "API и сервисы" → "Библиотека"
   - Найдите "YouTube Data API v3"
   - Нажмите "Включить"

4. **Создайте учетные данные:**
   - Перейдите в "API и сервисы" → "Учетные данные"
   - Нажмите "Создать учетные данные" → "Идентификатор клиента OAuth 2.0"
   - Выберите тип приложения "Веб-приложение"
   - В поле "Авторизованные URI перенаправления" добавьте: `http://localhost:8080/`
   - Нажмите "Создать"

5. **Скачайте JSON файл:**
   - После создания учетных данных нажмите "Скачать JSON"
   - Переименуйте скачанный файл в `client_secrets.json`
   - Поместите файл в корень проекта

### Шаги запуска

1. **Клонируйте репозиторий или скопируйте файлы проекта**

2. **Убедитесь, что файл `client_secrets.json` находится в корне проекта**
   - Этот файл необходим для авторизации в Google API
   - Получите его в Google Cloud Console

3. **Запустите проект:**
   ```bash
   docker-compose up --build
   ```

4. **Бот запустится автоматически**
   - Логи будут выводиться в консоль
   - Бот будет доступен в Telegram

### Остановка бота

- Нажмите `Ctrl+C` в терминале
- Или выполните:
  ```bash
  docker-compose down
  ```

## Структура проекта

- `bot_aiogram.py` - основной файл бота с логикой обработки сообщений и FSM
- `requirements.txt` - зависимости Python
- `.env` - переменные окружения (токен бота, необходимо создать)
- `.env.example` - пример файла переменных окружения
- `client_secrets.json` - секреты для Google API (необходимо добавить)
- `output/` - папка для выходных файлов (видео, логи)
- `sources/` - папка для исходных файлов (скачанные видео)
- `Dockerfile` - конфигурация Docker образа
- `docker-compose.yml` - конфигурация для запуска контейнера
- `.dockerignore` - файлы, исключаемые из Docker контекста
- `.gitignore` - файлы, исключаемые из Git репозитория

## Использование бота

После запуска бота в Telegram:

1. **Добавьте YouTube аккаунт**: Используйте команду для добавления учетных данных
2. **Создайте видео**: Отправьте ссылку на TikTok видео
3. **Бот обработает видео**: Скачает, смонтирует и загрузит на YouTube

## Переменные окружения

- `PYTHONUNBUFFERED=1` - для корректного вывода логов в Docker

## Volumes

В docker-compose.yml настроены volumes для:
- `./output:/app/output` - сохранение обработанных видео
- `./sources:/app/sources` - сохранение исходных файлов
- `./client_secrets.json:/app/client_secrets.json` - доступ к API ключам

## Разработка

Для локальной разработки без Docker:

1. Установите зависимости:
   ```bash
   pip install -r requirements.txt
   ```

2. Запустите бота:
   ```bash
   python bot_aiogram.py
   ```