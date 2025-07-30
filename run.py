# =================================================================================
#  VIDEO ARCHIVE BOT (GitHub Actions Edition)
#  Автор: Denlifik & AI Assistant
#  Версия: 1.0 (Стабильная)
#
#  Что делает этот скрипт:
#  1. Запускается на серверах GitHub по расписанию.
#  2. Проверяет RSS-ленту YouTube-канала на наличие новых видео.
#  3. Если есть новые видео, он скачивает их в качестве 480p, используя cookies
#     для аутентификации, чтобы обойти защиту YouTube от ботов.
#  4. С помощью FFmpeg нарезает каждое видео на 10-минутные куски.
#  5. Загружает каждый кусок в закрытый Telegram-канал.
#  6. Генерирует JSON-файл со списком всех видео и ID их частей в Telegram.
#  7. Сохраняет этот JSON-файл обратно в репозиторий GitHub.
# =================================================================================

import os
import json
import subprocess
import feedparser
from telegram import Bot
import asyncio
import time

# --- ГЛАВНЫЕ НАСТРОЙКИ ---
YOUTUBE_CHANNEL_URL = "https://www.youtube.com/feeds/videos.xml?channel_id=UCAvrIl6ltV8MdJo3mV4Nl4Q"
TEMP_FOLDER = 'temp_videos'      # Временная папка для файлов
DB_FILE = 'videos.json'          # Файл нашей базы данных
MAX_VIDEOS_ENTRIES = 25          # Сколько последних видео хранить в базе
CHUNK_DURATION_SECONDS = 600     # Длительность одного куска в секундах (600с = 10мин)
COOKIE_FILE = 'cookies.txt'      # Имя временного файла с cookies

# --- НАСТРОЙКИ ИЗ СЕКРЕТОВ GITHUB ---
# Эти переменные подставляются из Secrets репозитория
BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
CHANNEL_ID = os.environ.get('TELEGRAM_CHANNEL_ID')
YOUTUBE_COOKIES_DATA = os.environ.get('YOUTUBE_COOKIES')

# =================================================================================
# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
# =================================================================================

def get_video_db():
    """Читает JSON-файл с базой данных видео."""
    if os.path.exists(DB_FILE):
        with open(DB_FILE, 'r') as f:
            return json.load(f)
    return []

def save_video_db(db):
    """Сохраняет базу данных видео в JSON-файл."""
    with open(DB_FILE, 'w') as f:
        json.dump(db, f, indent=4)

async def upload_to_telegram(filepath, title):
    """Асинхронно загружает один видео-файл в Telegram."""
    print(f"  > Загружаю {os.path.basename(filepath)} в Telegram...")
    # Создаем новый объект Bot для каждой загрузки, чтобы избежать проблем с сетью
    bot = Bot(token=BOT_TOKEN)
    try:
        with open(filepath, 'rb') as video_file:
            message = await bot.send_video(
                chat_id=CHANNEL_ID, 
                video=video_file, 
                caption=title,
                # Увеличенные таймауты для больших файлов
                read_timeout=300, 
                write_timeout=300, 
                connect_timeout=300
            )
        # Обязательно закрываем сетевую сессию бота после использования
        await bot.shutdown()
        print(f"  ✅ Успешно загружено.")
        return message.video.file_id
    except Exception as e:
        print(f"  ❌ Ошибка при загрузке в Telegram: {e}")
        await bot.shutdown()
        return None

# =================================================================================
# --- ГЛАВНАЯ ЛОГИКА ОБРАБОТКИ ВИДЕО ---
# =================================================================================

async def process_video_async(video_id, title):
    """Полный цикл обработки одного видео: скачать -> нарезать -> загрузить."""
    print("-" * 50)
    print(f"🎬 Начинаю обработку видео: {title}")
    
    video_url = f"https://www.youtube.com/watch?v={video_id}"
    video_parts_info = []

    try:
        # --- ШАГ 1: СКАЧИВАНИЕ ---
        print("  [1/3] Скачиваю полное видео в 480p (с аутентификацией)...")
        temp_filepath_template = os.path.join(TEMP_FOLDER, f'{video_id}_full.%(ext)s')
        
        command_dl = [
            'yt-dlp',
            '--cookies', COOKIE_FILE,
            '--user-agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36',
            '--no-check-certificate',
            '-f', 'best[height<=480]', 
            '--output', temp_filepath_template, 
            video_url
        ]
        subprocess.run(command_dl, check=True, timeout=900)
        
        full_filename = next((f for f in os.listdir(TEMP_FOLDER) if f.startswith(f"{video_id}_full")), None)
        if not full_filename:
            print("  ❌ Ошибка: файл не скачался.")
            return None

        # --- ШАГ 2: НАРЕЗКА ---
        full_filepath = os.path.join(TEMP_FOLDER, full_filename)
        print("  ✅ Файл скачан. [2/3] Начинаю нарезку...")

        chunk_filename_template = os.path.join(TEMP_FOLDER, f"{video_id}_part_%03d.mp4")
        subprocess.run(['ffmpeg', '-i', full_filepath, '-c', 'copy', '-map', '0', '-segment_time', str(CHUNK_DURATION_SECONDS), '-f', 'segment', '-reset_timestamps', '1', chunk_filename_template], check=True, timeout=900)
        os.remove(full_filepath) # Сразу удаляем большой файл

        # --- ШАГ 3: ЗАГРУЗКА ---
        chunks = sorted([f for f in os.listdir(TEMP_FOLDER) if f.startswith(f"{video_id}_part_")])
        print(f"  ✅ Нарезано {len(chunks)} частей. [3/3] Загружаю в Telegram...")

        for i, chunk_filename in enumerate(chunks):
            chunk_filepath = os.path.join(TEMP_FOLDER, chunk_filename)
            part_title = f"{title} - Часть {i+1}"
            file_id = await upload_to_telegram(chunk_filepath, part_title)
            if file_id:
                video_parts_info.append({'part_num': i + 1, 'file_id': file_id})
            os.remove(chunk_filepath) # Сразу удаляем кусок
        
        if video_parts_info:
            print(f"🎉 Видео '{title}' полностью обработано!")
            return {'id': video_id, 'title': title, 'parts': video_parts_info}
        
        return None

    except Exception as e:
        print(f"❌ КРИТИЧЕСКАЯ ОШИБКА для '{title}': {e}")
        return None

# =================================================================================
# --- ТОЧКА ВХОДА И УПРАВЛЕНИЕ ---
# =================================================================================

def main():
    """Главная управляющая функция."""
    # Создаем необходимые папки
    if not os.path.exists(TEMP_FOLDER):
        os.makedirs(TEMP_FOLDER)
    
    # Создаем временный файл cookies.txt из секрета GitHub
    if YOUTUBE_COOKIES_DATA:
        with open(COOKIE_FILE, 'w') as f:
            f.write(YOUTUBE_COOKIES_DATA)
        print("ℹ️ Временный файл cookies.txt создан.")
    else:
        print("⚠️ ВНИМАНИЕ: Секрет YOUTUBE_COOKIES не найден. Скачивание может не удаться.")
    
    try:
        print("\n" + "="*50)
        print("🚀 Запуск проверки новых видео (GitHub Actions с Cookies)")
        print("="*50 + "\n")
        
        feed = feedparser.parse(YOUTUBE_CHANNEL_URL)
        if not feed.entries:
            print("RSS-лента пуста. Завершаю работу.")
            return

        db = get_video_db()
        existing_ids = {video['id'] for video in db}
        
        new_videos_to_process = [{'id': e.yt_videoid, 'title': e.title} for e in feed.entries if e.yt_videoid not in existing_ids]

        if not new_videos_to_process:
            print("✅ Новых видео не найдено. Все в актуальном состоянии.")
            return

        print(f"🔥 Найдено {len(new_videos_to_process)} новых видео для обработки.")
        
        processed_videos = []
        # Обрабатываем видео по одному
        for video_info in reversed(new_videos_to_process):
            result = asyncio.run(process_video_async(video_info['id'], video_info['title']))
            if result:
                processed_videos.append(result)
            time.sleep(5) # Небольшая пауза между обработкой видео
        
        # Если были обработаны новые видео, обновляем базу данных
        if processed_videos:
            final_db = processed_videos + db
            # Обрезаем базу, если она стала слишком длинной
            while len(final_db) > MAX_VIDEOS_ENTRIES:
                final_db.pop()
            save_video_db(final_db)
            print("\n" + "="*50)
            print("💾 База данных videos.json успешно обновлена в репозитории.")
            print("="*50)

    finally:
        # В любом случае удаляем временный файл с cookies в конце работы
        if os.path.exists(COOKIE_FILE):
            os.remove(COOKIE_FILE)
            print("ℹ️ Временный файл cookies.txt удален.")

if __name__ == '__main__':
    main()
