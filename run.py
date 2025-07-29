# Этот файл должен называться run.py
import os
import json
import subprocess
import feedparser
from telegram import Bot
import asyncio
import time

# --- НАСТРОЙКИ ---
YOUTUBE_CHANNEL_URL = "https://www.youtube.com/feeds/videos.xml?channel_id=UCAvrIl6ltV8MdJo3mV4Nl4Q" # Канал Куплинова
TEMP_FOLDER = 'temp_videos'
DB_FILE = 'videos.json' # Имя файла, который будет обновляться в репозитории
MAX_VIDEOS_ENTRIES = 25 # Сколько видео хранить в базе
CHUNK_DURATION_SECONDS = 600 # Режем на куски по 10 минут

# --- Настройки Telegram (берутся из Secrets GitHub) ---
BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
CHANNEL_ID = os.environ.get('TELEGRAM_CHANNEL_ID')

bot = Bot(token=BOT_TOKEN)

# --- Функции ---
def get_video_db():
    if os.path.exists(DB_FILE):
        with open(DB_FILE, 'r') as f: return json.load(f)
    return []

def save_video_db(db):
    with open(DB_FILE, 'w') as f: json.dump(db, f, indent=4)

async def upload_to_telegram(filepath, title):
    print(f"Загружаю {filepath} в Telegram...")
    try:
        with open(filepath, 'rb') as video_file:
            message = await bot.send_video(chat_id=CHANNEL_ID, video=video_file, caption=title, read_timeout=300, write_timeout=300, connect_timeout=300)
        return message.video.file_id
    except Exception as e:
        print(f"Ошибка при загрузке в Telegram: {e}")
        return None

async def process_video_async(video_id, title):
    print(f"Начинаю обработку видео: {title}")
    video_url = f"https://www.youtube.com/watch?v={video_id}"
    video_parts_info = []

    try:
        print("Скачиваю полное видео в 480p...")
        temp_filepath_template = os.path.join(TEMP_FOLDER, f'{video_id}_full.%(ext)s')
        subprocess.run(['yt-dlp', '-f', 'best[height<=480]', '--output', temp_filepath_template, video_url], check=True, timeout=900)
        
        full_filename = next((f for f in os.listdir(TEMP_FOLDER) if f.startswith(f"{video_id}_full")), None)
        if not full_filename: 
            print("Ошибка: файл не скачался.")
            return None

        full_filepath = os.path.join(TEMP_FOLDER, full_filename)
        print("Файл скачан. Начинаю нарезку...")

        chunk_filename_template = os.path.join(TEMP_FOLDER, f"{video_id}_part_%03d.mp4")
        subprocess.run(['ffmpeg', '-i', full_filepath, '-c', 'copy', '-map', '0', '-segment_time', str(CHUNK_DURATION_SECONDS), '-f', 'segment', '-reset_timestamps', '1', chunk_filename_template], check=True, timeout=900)
        os.remove(full_filepath)

        chunks = sorted([f for f in os.listdir(TEMP_FOLDER) if f.startswith(f"{video_id}_part_")])
        print(f"Нарезано {len(chunks)} частей. Загружаю...")

        for i, chunk_filename in enumerate(chunks):
            chunk_filepath = os.path.join(TEMP_FOLDER, chunk_filename)
            part_title = f"{title} - Часть {i+1}"
            file_id = await upload_to_telegram(chunk_filepath, part_title)
            if file_id:
                video_parts_info.append({'part_num': i + 1, 'file_id': file_id})
            os.remove(chunk_filepath)
        
        if video_parts_info:
            return {'id': video_id, 'title': title, 'parts': video_parts_info}
        return None

    except Exception as e:
        print(f"КРИТИЧЕСКАЯ ОШИБКА для '{title}': {e}")
        return None

def main():
    if not os.path.exists(TEMP_FOLDER): os.makedirs(TEMP_FOLDER)
    
    print("--- Запуск проверки новых видео (GitHub Actions) ---")
    feed = feedparser.parse(YOUTUBE_CHANNEL_URL)
    if not feed.entries: return

    db = get_video_db()
    existing_ids = {video['id'] for video in db}
    
    new_videos_to_process = [{'id': e.yt_videoid, 'title': e.title} for e in feed.entries if e.yt_videoid not in existing_ids]

    if not new_videos_to_process:
        print("Новых видео не найдено.")
        return

    print(f"Найдено {len(new_videos_to_process)} новых видео.")
    processed_videos = []
    for video_info in reversed(new_videos_to_process):
        result = asyncio.run(process_video_async(video_info['id'], video_info['title']))
        if result:
            processed_videos.append(result)
        time.sleep(5)
    
    if processed_videos:
        final_db = processed_videos + db
        while len(final_db) > MAX_VIDEOS_ENTRIES:
            final_db.pop()
        save_video_db(final_db)
        print("База данных videos.json успешно обновлена.")

if __name__ == '__main__':
    main()
