import os
import json
import subprocess
import feedparser
from telegram import Bot
import asyncio
import time

# --- НАСТРОЙКИ ---
YOUTUBE_CHANNEL_URL = "https://www.youtube.com/feeds/videos.xml?channel_id=UCAvrIl6ltV8MdJo3mV4Nl4Q"
TEMP_FOLDER = 'temp_videos'
DB_FILE = 'videos.json'
MAX_VIDEOS_ENTRIES = 25 
CHUNK_DURATION_SECONDS = 240 # 4 минуты
COOKIE_FILE = 'cookies.txt'

BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
CHANNEL_ID = os.environ.get('TELEGRAM_CHANNEL_ID')
YOUTUBE_COOKIES_DATA = os.environ.get('YOUTUBE_COOKIES')

# --- Функции ---
def get_video_db():
    if os.path.exists(DB_FILE):
        with open(DB_FILE, 'r') as f: return json.load(f)
    return []

def save_video_db(db):
    with open(DB_FILE, 'w') as f: json.dump(db, f, indent=4)

# --- НОВАЯ ФУНКЦИЯ: "Инспектор Качества" ---
def is_chunk_valid(filepath):
    """Проверяет, является ли видео-кусок корректным (имеет длительность > 0)."""
    try:
        command = [
            'ffprobe', '-v', 'error', '-show_entries', 'format=duration',
            '-of', 'default=noprint_wrappers=1:nokey=1', filepath
        ]
        result = subprocess.run(command, capture_output=True, text=True, check=True, timeout=60)
        duration = float(result.stdout.strip())
        if duration > 0:
            return True
        else:
            print(f"  ⚠️ Инспектор: Обнаружен кусок с нулевой длительностью: {os.path.basename(filepath)}")
            return False
    except Exception as e:
        print(f"  ⚠️ Инспектор: Ошибка при проверке куска {os.path.basename(filepath)}: {e}")
        return False

async def upload_to_telegram(filepath, title):
    print(f"  > Загружаю {os.path.basename(filepath)} в Telegram...")
    bot = Bot(token=BOT_TOKEN)
    try:
        with open(filepath, 'rb') as video_file:
            message = await bot.send_video(
                chat_id=CHANNEL_ID, video=video_file, caption=title, 
                read_timeout=300, write_timeout=300, connect_timeout=300
            )
        await bot.shutdown()
        print(f"  ✅ Успешно загружено.")
        return message.video.file_id
    except Exception as e:
        print(f"  ❌ Ошибка при загрузке в Telegram: {e}")
        await bot.shutdown()
        return None

async def process_video_async(video_id, title):
    print("-" * 50)
    print(f"🎬 Начинаю обработку видео: {title}")
    video_url = f"https://www.youtube.com/watch?v={video_id}"
    video_parts_info = []

    try:
        print("  [1/3] Скачиваю полное видео в 480p...")
        temp_filepath_template = os.path.join(TEMP_FOLDER, f'{video_id}_full.%(ext)s')
        
        command_dl = [
            'yt-dlp', '--cookies', COOKIE_FILE,
            '--user-agent', 'Mozilla/5.0 ...',
            '--no-check-certificate', '-f', 'best[height<=480]', 
            '--output', temp_filepath_template, video_url
        ]
        subprocess.run(command_dl, check=True, timeout=900)
        
        full_filename = next((f for f in os.listdir(TEMP_FOLDER) if f.startswith(f"{video_id}_full")), None)
        if not full_filename: return None

        full_filepath = os.path.join(TEMP_FOLDER, full_filename)
        print("  ✅ Файл скачан. [2/3] Начинаю чистую нарезку...")

        chunk_filename_template = os.path.join(TEMP_FOLDER, f"{video_id}_part_%03d.mp4")
        subprocess.run(['ffmpeg', '-i', full_filepath, '-c:v', 'libx264', '-preset', 'veryfast', '-c:a', 'aac', '-map', '0', '-segment_time', str(CHUNK_DURATION_SECONDS), '-f', 'segment', '-reset_timestamps', '1', '-movflags', '+faststart', chunk_filename_template], check=True, timeout=1800)
        os.remove(full_filepath)

        chunks = sorted([f for f in os.listdir(TEMP_FOLDER) if f.startswith(f"{video_id}_part_")])
        print(f"  ✅ Нарезано {len(chunks)} частей. [3/3] Проверяю и загружаю в Telegram...")

        for i, chunk_filename in enumerate(chunks):
            chunk_filepath = os.path.join(TEMP_FOLDER, chunk_filename)
            
            # --- ИЗМЕНЕНИЕ: ПРОВЕРЯЕМ КАЖДЫЙ КУСОК ПЕРЕД ЗАГРУЗКОЙ ---
            if is_chunk_valid(chunk_filepath):
                part_title = f"{title} - Часть {i+1}"
                file_id = await upload_to_telegram(chunk_filepath, part_title)
                if file_id:
                    video_parts_info.append({'part_num': i + 1, 'file_id': file_id})
            
            os.remove(chunk_filepath) # Удаляем в любом случае
        
        if video_parts_info:
            print(f"🎉 Видео '{title}' полностью обработано!")
            return {'id': video_id, 'title': title, 'parts': video_parts_info}
        else:
            print(f"⚠️ Для видео '{title}' не было создано ни одного корректного куска.")
            return None

    except Exception as e:
        print(f"❌ КРИТИЧЕСКАЯ ОШИБКА для '{title}': {e}")
        return None

# --- main() и if __name__ == '__main__' остаются такими же ---
def main():
    if not os.path.exists(TEMP_FOLDER): os.makedirs(TEMP_FOLDER)
    if YOUTUBE_COOKIES_DATA:
        with open(COOKIE_FILE, 'w') as f: f.write(YOUTUBE_COOKIES_DATA)
        print("ℹ️ Временный файл cookies.txt создан.")
    else:
        print("⚠️ ВНИМАНИЕ: Секрет YOUTUBE_COOKIES не найден.")
    try:
        print("\n" + "="*50); print("🚀 Запуск проверки новых видео (v5 - с Инспектором Качества)"); print("="*50 + "\n")
        feed = feedparser.parse(YOUTUBE_CHANNEL_URL)
        if not feed.entries: return
        db = get_video_db()
        existing_ids = {video['id'] for video in db}
        new_videos_to_process = [{'id': e.yt_videoid, 'title': e.title} for e in feed.entries if e.yt_videoid not in existing_ids]
        if not new_videos_to_process:
            print("✅ Новых видео не найдено."); return
        print(f"🔥 Найдено {len(new_videos_to_process)} новых видео для обработки.")
        processed_videos = []
        for video_info in reversed(new_videos_to_process):
            result = asyncio.run(process_video_async(video_info['id'], video_info['title']))
            if result: processed_videos.append(result)
            time.sleep(5)
        if processed_videos:
            final_db = processed_videos + db
            while len(final_db) > MAX_VIDEOS_ENTRIES: final_db.pop()
            save_video_db(final_db)
            print("\n" + "="*50); print("💾 База данных videos.json успешно обновлена."); print("="*50)
    finally:
        if os.path.exists(COOKIE_FILE):
            os.remove(COOKIE_FILE); print("ℹ️ Временный файл cookies.txt удален.")

if __name__ == '__main__':
    main()
