#!/usr/bin/env python3
"""Telegram Bot — TikTok → Short Video → YouTube"""

import os, sys, json, sqlite3, tempfile, subprocess, re, shutil, time, random, asyncio, html
from pathlib import Path
from datetime import datetime
from typing import Optional, Tuple
import logging

# ─── Bootstrap ────────────────────────────────────────────────────────────────
def _bootstrap():
    need = []
    for pkg, imp in [
        ("aiogram", "aiogram"),
        ("google-auth-oauthlib", "google_auth_oauthlib"),
        ("google-api-python-client", "googleapiclient"),
        ("yt-dlp", "yt_dlp"),
        ("faster-whisper", "faster_whisper"),
    ]:
        try: __import__(imp)
        except ImportError: need.append(pkg)
    if need:
        print(f"Installing: {', '.join(need)}...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-q"] + need)
_bootstrap()

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, FSInputFile, BufferedInputFile
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
import yt_dlp

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# STATES
# ═══════════════════════════════════════════════════════════════════════════════

class AddAccountState(StatesGroup):
    waiting_json = State()
    waiting_name = State()

class CreateVideoState(StatesGroup):
    waiting_account  = State()
    waiting_tiktok_url = State()
    waiting_auth_code  = State()
    waiting_description = State()

class DownloadSourcesState(StatesGroup):
    waiting_urls = State()

# ═══════════════════════════════════════════════════════════════════════════════
# DATABASE
# ═══════════════════════════════════════════════════════════════════════════════

class Database:
    def __init__(self, path: str = "videos.db"):
        self.path = path
        self._init()

    def _init(self):
        conn = sqlite3.connect(self.path)
        c = conn.cursor()
        c.execute("""CREATE TABLE IF NOT EXISTS youtube_accounts (
            id INTEGER PRIMARY KEY,
            name TEXT UNIQUE NOT NULL,
            credentials TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS videos (
            id INTEGER PRIMARY KEY,
            title TEXT NOT NULL,
            filepath TEXT NOT NULL,
            youtube_account_id INTEGER,
            status TEXT DEFAULT 'pending',
            youtube_url TEXT,
            youtube_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(youtube_account_id) REFERENCES youtube_accounts(id)
        )""")
        conn.commit()
        conn.close()

    def add_account(self, name: str, credentials_json: str) -> bool:
        conn = sqlite3.connect(self.path)
        try:
            conn.execute("INSERT INTO youtube_accounts (name, credentials) VALUES (?, ?)", (name, credentials_json))
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False
        finally:
            conn.close()

    def get_accounts(self):
        conn = sqlite3.connect(self.path)
        rows = conn.execute("SELECT id, name FROM youtube_accounts").fetchall()
        conn.close()
        return rows

    def get_account_creds(self, account_id: int) -> Optional[str]:
        conn = sqlite3.connect(self.path)
        row = conn.execute("SELECT credentials FROM youtube_accounts WHERE id = ?", (account_id,)).fetchone()
        conn.close()
        return row[0] if row else None

    def update_account_creds(self, account_id: int, creds_json: str):
        conn = sqlite3.connect(self.path)
        conn.execute("UPDATE youtube_accounts SET credentials = ? WHERE id = ?", (creds_json, account_id))
        conn.commit()
        conn.close()

    def add_video(self, title: str, filepath: str, account_id: int) -> int:
        conn = sqlite3.connect(self.path)
        vid = conn.execute(
            "INSERT INTO videos (title, filepath, youtube_account_id) VALUES (?, ?, ?)",
            (title, filepath, account_id)
        ).lastrowid
        conn.commit()
        conn.close()
        return vid

    def update_video_status(self, video_id: int, status: str, yt_url: str = None, yt_id: str = None):
        conn = sqlite3.connect(self.path)
        conn.execute(
            "UPDATE videos SET status = ?, youtube_url = ?, youtube_id = ? WHERE id = ?",
            (status, yt_url, yt_id, video_id)
        )
        conn.commit()
        conn.close()

# ═══════════════════════════════════════════════════════════════════════════════
# VIDEO PROCESSING  (download → render with subtitles → preview)
# ═══════════════════════════════════════════════════════════════════════════════

class VideoProcessor:

    AUDIO_EXTS = (".mp3", ".m4a", ".aac", ".opus", ".webm", ".ogg", ".wav", ".mp4", ".mkv", ".ts")

    @staticmethod
    def _sanitize(name: str) -> str:
        """Для имени файла: спецсимволы убираем, пробелы → _"""
        return re.sub(r'[\\/*?:"<>|]', "", name).strip().replace(" ", "_")[:60] or "video"

    @staticmethod
    def _display_title(name: str) -> str:
        """Для YouTube/показа: читаемый вид, #тег_#тег2 → #тег #тег2"""
        clean = re.sub(r'[\\/*?:"<>|]', "", name).strip()
        clean = re.sub(r'_(?=#)', " ", clean)   # #тег_#тег → #тег #тег
        clean = re.sub(r'_', " ", clean)         # остальные _ тоже в пробел
        clean = re.sub(r'\s+', " ", clean).strip()
        return clean[:100] or "video"

    @staticmethod
    def _probe_duration(path: str) -> float:
        try:
            r = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", path],
                capture_output=True, text=True, timeout=10
            )
            return float(r.stdout.strip() or 0)
        except:
            return 0.0

    @staticmethod
    def _find_file(info: dict, tmp_dir: str, exts: tuple) -> Optional[str]:
        def check(p):
            if not p: return None
            p = os.path.normpath(os.path.join(tmp_dir, p) if not os.path.isabs(p) else p)
            return p if os.path.exists(p) else None

        for key in ("requested_downloads", "requested_formats"):
            items = info.get(key)
            if isinstance(items, dict): items = [items]
            if isinstance(items, list):
                for item in (items or []):
                    for f in ("filepath", "filename", "_filename"):
                        if r := check((item or {}).get(f)): return r

        for f in ("filepath", "filename", "_filename"):
            if r := check(info.get(f)): return r

        for root, _, files in os.walk(tmp_dir):
            for f in files:
                if f.lower().endswith(exts): return os.path.join(root, f)
        for root, _, files in os.walk(tmp_dir):
            for f in files:
                if not f.lower().endswith((".part", ".tmp")): return os.path.join(root, f)
        return None

    @staticmethod
    def download_audio(tiktok_url: str, tmp_dir: str) -> Tuple[Optional[str], Optional[str], float, str]:
        opts = {
            "format": "bestaudio/best",
            "outtmpl": os.path.join(tmp_dir, "%(title)s.%(ext)s"),
            "quiet": True, "no_warnings": True,
            "noplaylist": True, "restrictfilenames": True,
        }
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(tiktok_url, download=True) or {}
                if info.get("entries"):
                    info = info["entries"][0] or {}
                title = info.get("title", "tiktok")
                dur   = float(info.get("duration") or 0)
                audio = VideoProcessor._find_file(info, tmp_dir, VideoProcessor.AUDIO_EXTS)
                if not audio:
                    logger.error("No audio found; tmp=%s", list(Path(tmp_dir).rglob("*")))
                    return None, None, 0.0, ""
            return audio, VideoProcessor._sanitize(title), dur, VideoProcessor._display_title(title)
        except Exception:
            logger.exception("download_audio failed")
            return None, None, 0.0

    @staticmethod
    def get_background_video(sources_dir: str) -> Optional[str]:
        if not os.path.isdir(sources_dir):
            return None
        videos = [f for f in os.listdir(sources_dir) if f.lower().endswith(('.mp4', '.mkv', '.webm'))]
        return os.path.join(sources_dir, random.choice(videos)) if videos else None

    @staticmethod
    def _build_bg_for_duration(sources_dir: str, audio_dur: float, tmp_dir: str) -> Optional[str]:
        """
        Собирает фоновое видео нужной длины:
        - берёт случайные сурсы без повторов пока не наберём audio_dur секунд
        - склеивает через ffmpeg concat если нужно больше одного
        - возвращает путь к готовому фону (может быть один файл или склеенный)
        """
        if not os.path.isdir(sources_dir):
            return None
        all_videos = [
            os.path.join(sources_dir, f)
            for f in os.listdir(sources_dir)
            if f.lower().endswith(('.mp4', '.mkv', '.webm'))
        ]
        if not all_videos:
            return None

        # Перемешиваем и набираем пока не покроем длину аудио
        pool = all_videos.copy()
        random.shuffle(pool)
        selected = []
        total = 0.0

        while total < audio_dur:
            if not pool:
                # Все сурсы использованы — берём снова (перемешанные)
                pool = all_videos.copy()
                random.shuffle(pool)
                # Исключаем последний чтобы не было одинаковых подряд
                if selected and len(pool) > 1:
                    pool = [v for v in pool if v != selected[-1]]

            v = pool.pop(0)
            dur = VideoProcessor._probe_duration(v)
            if dur <= 0:
                continue
            selected.append(v)
            total += dur

        if not selected:
            return None

        # Один файл — возвращаем как есть
        if len(selected) == 1:
            return selected[0]

        # Несколько — склеиваем через ffmpeg concat
        list_path = os.path.join(tmp_dir, "bg_list.txt")
        with open(list_path, "w") as f:
            for v in selected:
                f.write(f"file '{v}'\n")

        concat_path = os.path.join(tmp_dir, "bg_concat.mp4")
        cmd = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", list_path,
            "-c", "copy",
            concat_path,
        ]
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=300)
            if r.returncode == 0 and os.path.exists(concat_path):
                return concat_path
        except Exception as e:
            logger.error("concat bg error: %s", e)

        # Fallback: просто первый файл
        return selected[0]

    # ── Core render: crop to 9:16, add subtitles ──────────────────────────────

    @staticmethod
    def _extract_subtitles(audio_path: str, srt_path: str,
                           on_progress=None, audio_dur: float = 0.0) -> bool:
        """
        faster-whisper tiny, 2 words/cue, CPU 75%.
        on_progress(pct: float, eta_sec: float) вызывается на каждом сегменте.
        """
        try:
            from faster_whisper import WhisperModel  # type: ignore
        except ImportError:
            logger.warning("faster-whisper not installed; skipping subtitles")
            open(srt_path, "w").close()
            return False

        def fmt(t: float) -> str:
            h, r = divmod(int(t), 3600)
            m, s = divmod(r, 60)
            ms = int((t - int(t)) * 1000)
            return f"{h:02}:{m:02}:{s:02},{ms:03}"

        try:
            threads = max(1, int((os.cpu_count() or 4) * 0.75))
            model = WhisperModel("tiny", device="cpu", compute_type="int8",
                                 cpu_threads=threads, num_workers=1)

            segments_iter, _ = model.transcribe(audio_path, word_timestamps=True)

            cues = []
            t0 = time.time()
            total = audio_dur or 1.0

            for seg in segments_iter:
                words = list(seg.words or [])
                if not words:
                    tokens = seg.text.strip().split()
                    seg_dur = seg.end - seg.start
                    step = seg_dur / max(len(tokens), 1)
                    words = [
                        type("W", (), {"word": w,
                                       "start": seg.start + i * step,
                                       "end":   seg.start + (i + 1) * step})()
                        for i, w in enumerate(tokens)
                    ]

                for i in range(0, len(words), 2):
                    chunk = words[i:i + 2]
                    text  = " ".join(w.word.strip() for w in chunk)
                    start, end = chunk[0].start, chunk[-1].end
                    if text.strip():
                        cues.append((start, end, text))

                # прогресс
                if on_progress and total > 0:
                    pct = min(seg.end / total, 1.0)
                    elapsed = time.time() - t0
                    eta = (elapsed / pct * (1 - pct)) if pct > 0.01 else 0.0
                    on_progress(pct, eta)

            with open(srt_path, "w", encoding="utf-8") as f:
                for idx, (start, end, text) in enumerate(cues, 1):
                    f.write(f"{idx}\n{fmt(start)} --> {fmt(end)}\n{text}\n\n")

            if on_progress:
                on_progress(1.0, 0.0)

            return os.path.exists(srt_path) and os.path.getsize(srt_path) > 10

        except Exception as e:
            logger.warning("faster-whisper failed: %s", e)
            open(srt_path, "w").close()
            return False

    @staticmethod
    def render_video(bg_path: str, audio_path: str, output_path: str,
                     srt_path: Optional[str] = None,
                     on_progress=None) -> bool:
        """
        Render 9:16 + subtitles + trim to audio.
        on_progress(pct, eta_sec) вызывается по мере рендера через ffmpeg -progress.
        """
        audio_dur = VideoProcessor._probe_duration(audio_path)

        vf_parts = [
            "crop=ih*9/16:ih:(iw-ih*9/16)/2:0",
            "scale=1080:1920:force_original_aspect_ratio=decrease",
            "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black",
        ]

        has_subs = srt_path and os.path.exists(srt_path) and os.path.getsize(srt_path) > 10
        if has_subs:
            safe_srt = srt_path.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
            vf_parts.append(
                f"subtitles='{safe_srt}':force_style='"
                "FontName=Arial,FontSize=18,Bold=1,"
                "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,"
                "Outline=2,Shadow=1,Alignment=2,MarginV=60'"
            )

        vf = ",".join(vf_parts)

        cmd = [
            "ffmpeg", "-y",
            "-i", bg_path, "-i", audio_path,
            "-vf", vf,
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "aac", "-b:a", "192k",
            "-map", "0:v:0", "-map", "1:a:0",
            "-progress", "pipe:1", "-nostats",
        ]
        if audio_dur > 0:
            cmd += ["-t", str(audio_dur)]
        cmd.append(output_path)

        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                    text=True)
            t0 = time.time()
            total = audio_dur or 1.0

            for line in proc.stdout:
                line = line.strip()
                if line.startswith("out_time_us="):
                    try:
                        us = int(line.split("=", 1)[1])
                        pct = min(us / 1_000_000 / total, 1.0)
                        elapsed = time.time() - t0
                        eta = (elapsed / pct * (1 - pct)) if pct > 0.01 else 0.0
                        if on_progress:
                            on_progress(pct, eta)
                    except ValueError:
                        pass

            proc.wait()
            if on_progress:
                on_progress(1.0, 0.0)
            return proc.returncode == 0 and os.path.exists(output_path)

        except Exception as e:
            logger.error("render_video error: %s", e)
            return False

    @staticmethod
    def make_preview(video_path: str, preview_path: str, duration: int = 10) -> bool:
        """Cut first N seconds, scale to 240p — for quick preview in Telegram."""
        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-t", str(duration),
            "-vf", "scale=-2:240",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30",
            "-c:a", "aac", "-b:a", "64k",
            preview_path,
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=60)
            return result.returncode == 0 and os.path.exists(preview_path)
        except Exception as e:
            logger.error("make_preview error: %s", e)
            return False

    @staticmethod
    def download_sources(urls: list, sources_dir: str) -> Tuple[int, int]:
        ok = fail = 0
        for url in urls:
            try:
                opts = {
                    "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
                    "outtmpl": os.path.join(sources_dir, "%(title)s.%(ext)s"),
                    "quiet": True, "no_warnings": True, "noplaylist": True,
                    "merge_output_format": "mp4",
                }
                with yt_dlp.YoutubeDL(opts) as ydl:
                    ydl.download([url])
                ok += 1
            except Exception as e:
                logger.error("download_sources error: %s", e)
                fail += 1
        return ok, fail

# ═══════════════════════════════════════════════════════════════════════════════
# YOUTUBE UPLOAD
# ═══════════════════════════════════════════════════════════════════════════════

class YouTubeUploader:
    SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

    def __init__(self, credentials_json: str):
        self.service = None
        try:
            creds = Credentials.from_authorized_user_info(json.loads(credentials_json), self.SCOPES)
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
            self.service = build("youtube", "v3", credentials=creds)
        except Exception as e:
            logger.error("YouTube auth error: %s", e)

    def upload(self, video_path: str, title: str, description: str = "") -> Optional[str]:
        if not self.service or not os.path.exists(video_path):
            return None
        try:
            media = MediaFileUpload(video_path, mimetype="video/mp4", resumable=True, chunksize=1024 * 1024)
            body = {
                "snippet": {"title": title[:100], "description": description[:5000],
                            "tags": ["shorts"], "categoryId": "22"},
                "status": {"privacyStatus": "public"},
            }
            req = self.service.videos().insert(part="snippet,status", body=body, media_body=media)
            response = None
            while response is None:
                _, response = req.next_chunk()
            return response.get("id")
        except Exception as e:
            logger.error("YouTube upload error: %s", e)
            return None

# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS / UI
# ═══════════════════════════════════════════════════════════════════════════════

def kb(*rows):
    """Build InlineKeyboardMarkup from row tuples: ((text, data), ...)"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t, callback_data=d) for t, d in row]
        for row in rows
    ])

def pbar(pct: float, eta: float, label: str, width: int = 12) -> str:
    """
    Рисует прогресс-бар из символов + ETA.
    Пример: Субтитры
            ████████░░░░  67% · ~14с
    """
    filled = int(pct * width)
    bar = "█" * filled + "░" * (width - filled)
    pct_str = f"{int(pct * 100)}%"
    if eta > 1:
        m, s = divmod(int(eta), 60)
        eta_str = f"~{m}м {s}с" if m else f"~{s}с"
    else:
        eta_str = "готово" if pct >= 1.0 else "..."
    return f"<b>{label}</b>\n{bar}  {pct_str} · {eta_str}"

MAIN_KB = kb(
    (("📹  Создать видео", "create"),),
    (("🔐  Аккаунты YouTube", "accounts"),),
    (("📥  Загрузить фоны", "download_sources"),),
)

def main_text():
    return "Главное меню"

# ═══════════════════════════════════════════════════════════════════════════════
# BOT
# ═══════════════════════════════════════════════════════════════════════════════

class TelegramBot:
    def __init__(self, token: str):
        self.bot = Bot(token=token)
        self.dp  = Dispatcher()
        self.db  = Database()
        self.sources_dir = "./sources"
        self.output_dir  = "./output"
        os.makedirs(self.sources_dir, exist_ok=True)
        os.makedirs(self.output_dir,  exist_ok=True)
        self._register()

    def _register(self):
        dp = self.dp

        dp.message.register(self.cmd_start, Command("start"))

        dp.callback_query.register(self.cb_create,           F.data == "create")
        dp.callback_query.register(self.cb_accounts,         F.data == "accounts")
        dp.callback_query.register(self.cb_add_account,      F.data == "add_account")
        dp.callback_query.register(self.cb_download_sources, F.data == "download_sources")
        dp.callback_query.register(self.cb_back,             F.data == "back")
        dp.callback_query.register(self.cb_select_account,   F.data.startswith("sel_acc_"))
        dp.callback_query.register(self.cb_confirm_upload,   F.data == "confirm_upload")
        dp.callback_query.register(self.cb_cancel,           F.data == "cancel")

        dp.message.register(self.msg_json,           AddAccountState.waiting_json)
        dp.message.register(self.msg_acc_name,       AddAccountState.waiting_name)
        dp.message.register(self.msg_tiktok_url,     CreateVideoState.waiting_tiktok_url)
        dp.message.register(self.msg_auth_code,      CreateVideoState.waiting_auth_code)
        dp.message.register(self.msg_description,    CreateVideoState.waiting_description)
        dp.message.register(self.msg_source_urls,    DownloadSourcesState.waiting_urls)

    # ── /start ─────────────────────────────────────────────────────────────────

    async def cmd_start(self, msg: types.Message):
        await msg.answer("🤖 <b>TikTok → Shorts</b>", reply_markup=MAIN_KB, parse_mode="HTML")

    # ── Навигация ──────────────────────────────────────────────────────────────

    async def cb_back(self, q: types.CallbackQuery, state: FSMContext):
        await state.clear()
        await q.message.edit_text("🤖 <b>TikTok → Shorts</b>", reply_markup=MAIN_KB, parse_mode="HTML")

    async def cb_cancel(self, q: types.CallbackQuery, state: FSMContext):
        await state.clear()
        await q.message.edit_text("🤖 <b>TikTok → Shorts</b>", reply_markup=MAIN_KB, parse_mode="HTML")

    # ── Создать видео ──────────────────────────────────────────────────────────

    async def cb_create(self, q: types.CallbackQuery, state: FSMContext):
        accounts = self.db.get_accounts()
        if not accounts:
            await q.answer("Нет аккаунтов YouTube", show_alert=True)
            return
        rows = [((f"▶  {name}", f"sel_acc_{aid}"),) for aid, name in accounts]
        rows.append((("✕  Отмена", "back"),))
        await q.message.edit_text("Выбери аккаунт YouTube:", reply_markup=kb(*rows))

    async def cb_select_account(self, q: types.CallbackQuery, state: FSMContext):
        account_id = int(q.data.split("_")[-1])
        creds_json = self.db.get_account_creds(account_id)
        if not creds_json:
            await q.answer("Credentials не найдены", show_alert=True)
            return

        try:
            creds = Credentials.from_authorized_user_info(
                json.loads(creds_json), ["https://www.googleapis.com/auth/youtube.upload"])
            if creds.valid or (creds.expired and creds.refresh_token):
                await state.update_data(account_id=account_id)
                await state.set_state(CreateVideoState.waiting_tiktok_url)
                await q.message.edit_text("Отправь ссылку TikTok:")
                return
        except:
            pass

        # Нужна OAuth авторизация
        try:
            creds_dict = json.loads(creds_json)
            flow = InstalledAppFlow.from_client_config(
                creds_dict, ["https://www.googleapis.com/auth/youtube.upload"])
            flow.redirect_uri = "http://localhost:8080/"
            auth_url, _ = flow.authorization_url()
            await state.update_data(account_id=account_id, flow=flow)
            await state.set_state(CreateVideoState.waiting_auth_code)
            await q.message.edit_text(
                f"Авторизуйся по ссылке:\n\n<code>{auth_url}</code>\n\n"
                "Затем скопируй URL из адресной строки и отправь сюда.",
                parse_mode="HTML"
            )
        except Exception as e:
            await q.answer(f"Ошибка: {e}", show_alert=True)

    async def msg_auth_code(self, msg: types.Message, state: FSMContext):
        url = msg.text.strip()
        data = await state.get_data()
        flow, account_id = data.get("flow"), data.get("account_id")
        if not flow or not account_id:
            await msg.answer("Сессия истекла. Начни заново.")
            await state.clear()
            return
        try:
            from urllib.parse import urlparse, parse_qs
            code = parse_qs(urlparse(url).query).get("code", [None])[0]
            if not code:
                await msg.answer("Не найден code в URL.")
                return
            flow.fetch_token(code=code)
            self.db.update_account_creds(account_id, flow.credentials.to_json())
            await state.update_data(account_id=account_id)
            await state.set_state(CreateVideoState.waiting_tiktok_url)
            await msg.answer("✅ Авторизация успешна!\n\nОтправь ссылку TikTok:")
        except Exception as e:
            logger.error("auth_code error: %s", e)
            await msg.answer(f"Ошибка: {e}")
            await state.clear()

    async def msg_tiktok_url(self, msg: types.Message, state: FSMContext):
        url = msg.text.strip()
        if not ("tiktok.com" in url or "vm.tiktok.com" in url):
            await msg.answer("Неверная ссылка TikTok.")
            return

        data = await state.get_data()
        account_id = data.get("account_id")
        status_msg = await msg.answer("⏳ Скачиваю аудио...")

        # throttle: не спамим edit_text чаще раза в N секунд
        _last_edit = [0.0]
        async def safe_edit(text: str, force: bool = False):
            now = time.time()
            if force or now - _last_edit[0] >= 1.5:
                try:
                    await status_msg.edit_text(text, parse_mode="HTML")
                    _last_edit[0] = now
                except Exception:
                    pass

        loop = asyncio.get_event_loop()

        try:
            with tempfile.TemporaryDirectory() as tmp:
                # 1. Download audio
                audio, title, dur, display_title = await asyncio.to_thread(
                    VideoProcessor.download_audio, url, tmp)
                if not audio:
                    await safe_edit("❌ Не удалось скачать аудио.", force=True)
                    await state.clear()
                    return
                if not dur:
                    dur = VideoProcessor._probe_duration(audio)

                # 2. Background
                await safe_edit("⏳ Подбираю фон...", force=True)
                bg = await asyncio.to_thread(
                    VideoProcessor._build_bg_for_duration, self.sources_dir, dur, tmp)
                if not bg:
                    await safe_edit("❌ Нет фоновых видео.\n\nЗагрузи через «📥 Загрузить фоны».", force=True)
                    await state.clear()
                    return

                # 3. Subtitles с живым прогрессом
                srt_path = os.path.join(tmp, "subs.srt")

                def on_subs(pct, eta):
                    asyncio.run_coroutine_threadsafe(
                        safe_edit(pbar(pct, eta, "🔤 Субтитры")), loop)

                await safe_edit(pbar(0, 0, "🔤 Субтитры"), force=True)
                has_subs = await asyncio.to_thread(
                    VideoProcessor._extract_subtitles, audio, srt_path, on_subs, dur)

                # 4. Render с живым прогрессом
                output_path = os.path.join(self.output_dir, f"{title}.mp4")
                os.makedirs(self.output_dir, exist_ok=True)

                def on_render(pct, eta):
                    asyncio.run_coroutine_threadsafe(
                        safe_edit(pbar(pct, eta, "🎬 Рендер")), loop)

                await safe_edit(pbar(0, 0, "🎬 Рендер"), force=True)
                ok = await asyncio.to_thread(
                    VideoProcessor.render_video, bg, audio, output_path,
                    srt_path if has_subs else None, on_render)

                if not ok:
                    await safe_edit("❌ Ошибка рендера.", force=True)
                    await state.clear()
                    return

                # 5. Preview
                await safe_edit("⏳ Создаю превью...", force=True)
                preview_path = os.path.join(tmp, "preview.mp4")
                has_preview = await asyncio.to_thread(
                    VideoProcessor.make_preview, output_path, preview_path)

                # 6. Отправляем результат
                await state.update_data(output_path=output_path, title=title, display_title=display_title or title.replace("_"," "), account_id=account_id)
                await state.set_state(CreateVideoState.waiting_description)

                caption = (
                    f"✅ Готово!\n\n"
                    f"<b>{html.escape(display_title or title)}</b>\n"
                    f"{'🔤 Субтитры: да' if has_subs else '🔤 Субтитры: нет'}\n\n"
                    "Отправь описание или пропусти:"
                )
                confirm_kb = kb(
                    (("Без описания →", "confirm_upload"),),
                    (("✕  Отмена", "cancel"),),
                )

                await status_msg.delete()
                if has_preview:
                    with open(preview_path, "rb") as f:
                        preview_data = f.read()
                    await msg.answer_video(
                        BufferedInputFile(preview_data, filename="preview.mp4"),
                        caption=caption,
                        parse_mode="HTML",
                        reply_markup=confirm_kb,
                    )
                else:
                    await msg.answer(caption, parse_mode="HTML", reply_markup=confirm_kb)

        except Exception as e:
            logger.exception("msg_tiktok_url error")
            await safe_edit(f"❌ Ошибка: {str(e)[:200]}", force=True)
            await state.clear()

    async def msg_description(self, msg: types.Message, state: FSMContext):
        description = msg.text.strip()
        data = await state.get_data()
        await self._upload_and_report(msg, state, data, description)

    async def cb_confirm_upload(self, q: types.CallbackQuery, state: FSMContext):
        data = await state.get_data()
        # Убираем кнопки с превью
        try:
            await q.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        status_msg = await q.message.answer("⏳ Публикую на YouTube...")
        await self._upload_and_report(status_msg, state, data, "", is_edit=True)

    async def _upload_and_report(self, target, state, data, description, is_edit=False):
        output_path    = data.get("output_path")
        title          = data.get("title")           # filename-safe
        display_title  = data.get("display_title") or (title or "").replace("_", " ")
        account_id     = data.get("account_id")

        if not (output_path and title and account_id):
            text = "Сессия истекла. Начни заново."
            if is_edit:
                try: await target.edit_text(text)
                except: pass
            else:
                await target.answer(text)
            await state.clear()
            return

        async def _set(text):
            if is_edit:
                try: await target.edit_text(text, parse_mode="HTML")
                except: pass
            else:
                await target.answer(text, parse_mode="HTML")

        await _set("⏳ Загружаю на YouTube...")

        video_id = self.db.add_video(display_title, output_path, account_id)
        creds_json = self.db.get_account_creds(account_id)
        uploader = YouTubeUploader(creds_json)
        yt_id = uploader.upload(output_path, display_title, description)

        if yt_id:
            yt_url = f"https://youtube.com/shorts/{yt_id}"
            self.db.update_video_status(video_id, "published", yt_url, yt_id)
            await _set(
                f"✅ Опубликовано!\n\n"
                f"<b>{html.escape(display_title)}</b>\n"
                f"<a href='{yt_url}'>Смотреть на YouTube Shorts</a>"
            )
            if not is_edit:
                pass
        else:
            self.db.update_video_status(video_id, "pending")
            await _set("⚠️ Видео создано, но не загружено на YouTube.")

        await state.clear()

    # ── Аккаунты ──────────────────────────────────────────────────────────────

    async def cb_accounts(self, q: types.CallbackQuery):
        accounts = self.db.get_accounts()
        if accounts:
            lines = "\n".join(f"  · {name}" for _, name in accounts)
            text = f"<b>YouTube аккаунты</b>\n\n{lines}"
        else:
            text = "<b>YouTube аккаунты</b>\n\nПока пусто."
        menu = kb(
            (("➕  Добавить аккаунт", "add_account"),),
            (("⬅  Назад", "back"),),
        )
        await q.message.edit_text(text, reply_markup=menu, parse_mode="HTML")

    async def cb_add_account(self, q: types.CallbackQuery, state: FSMContext):
        await state.set_state(AddAccountState.waiting_json)
        await q.message.edit_text(
            "<b>Добавить аккаунт</b>\n\n"
            "Скачай <code>client_secrets.json</code> из Google Cloud Console\n"
            "и отправь содержимое файла:",
            parse_mode="HTML"
        )

    async def msg_json(self, msg: types.Message, state: FSMContext):
        try:
            creds = json.loads(msg.text)
            if "installed" not in creds:
                raise ValueError("bad format")
            await state.update_data(credentials=msg.text)
            await state.set_state(AddAccountState.waiting_name)
            await msg.answer("Придумай название для аккаунта:")
        except (json.JSONDecodeError, ValueError):
            await msg.answer("❌ Неверный JSON. Попробуй снова.")

    async def msg_acc_name(self, msg: types.Message, state: FSMContext):
        name = msg.text.strip()
        data = await state.get_data()
        if self.db.add_account(name, data["credentials"]):
            await msg.answer(f"✅ Аккаунт <b>{name}</b> добавлен.", parse_mode="HTML",
                             reply_markup=MAIN_KB)
        else:
            await msg.answer("Аккаунт с таким именем уже существует.")
        await state.clear()

    # ── Загрузка фонов ────────────────────────────────────────────────────────

    async def cb_download_sources(self, q: types.CallbackQuery, state: FSMContext):
        await state.set_state(DownloadSourcesState.waiting_urls)
        await q.message.edit_text(
            "<b>Загрузить фоновые видео</b>\n\n"
            "Отправляй ссылки (по одной или списком).\n"
            "Чтобы выйти — напиши <code>стоп</code>.",
            parse_mode="HTML"
        )

    async def msg_source_urls(self, msg: types.Message, state: FSMContext):
        text = msg.text.strip()
        if text.lower() in ("стоп", "stop", "exit", "отмена"):
            await state.clear()
            await msg.answer("✅ Загрузка фонов завершена.", reply_markup=MAIN_KB)
            return

        urls = [u.strip() for u in text.splitlines() if u.strip()]
        if not urls:
            await msg.answer("Нет ссылок.")
            return

        total = len(urls)
        progress = await msg.answer(f"⏳ Скачиваю 0/{total}...")
        success = 0

        for i, url in enumerate(urls, 1):
            await progress.edit_text(f"⏳ Скачиваю {i}/{total}…\n<code>{url[:60]}</code>",
                                     parse_mode="HTML")
            ok, _ = VideoProcessor.download_sources([url], self.sources_dir)
            if ok:
                success += 1

        mark = "✅" if success == total else "⚠️"
        await progress.edit_text(f"{mark} Загружено {success}/{total} видео.")

    # ── Run ───────────────────────────────────────────────────────────────────

    async def run(self):
        await self.dp.start_polling(self.bot)


async def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        print("❌  Установи TELEGRAM_BOT_TOKEN")
        print("    export TELEGRAM_BOT_TOKEN='your_token_here'")
        return
    bot = TelegramBot(token)
    print("🤖 Бот запущен...")
    await bot.run()


if __name__ == "__main__":
    asyncio.run(main())