# =================================================================================
#  VIDEO ARCHIVE BOT (GitHub Actions Edition)
#  Версия: 2.0 (с функцией "Антиспойлер")
# =================================================================================

import os
import json
import subprocess
import feedparser
from telegram import Bot
import asyncio
import time
import pytesseract
from PIL import Image
import google.generativeai as genai
import re

# --- НАСТРОЙКИ ---
YOUTUBE_CHANNEL_URL = "https://www.youtube.com/feeds/videos.xml?channel_id=UCAvrIl6ltV8MdJo3mV4Nl4Q"
TEMP_FOLDER = 'temp_videos'
DB_FILE = 'videos.json'
MAX_VIDEOS_ENTRIES = 25
CHUNK_DURATION_SECONDS = 600
COOKIE_FILE = 'cookies.txt'
OCR_DURATION_SECONDS = 45 # Сколько секунд видео анализировать на спойлеры

# --- Настройки из Secrets ---
BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
CHANNEL_ID = os.environ.get('TELEGRAM_CHANNEL_ID')
YOUTUBE_COOKIES_DATA = os.environ.get('YOUTUBE_COOKIES')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')

# Конфигурируем Gemini
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

# --- Функции ---
def get_video_db():
    if os.path.exists(DB_FILE):
        with open(DB_FILE, 'r') as f: return json.load(f)
    return []

def save_video_db(db):
    with open(DB_FILE, 'w') as f: json.dump(db, f, indent=4)

async def upload_to_telegram(filepath, title):
    print(f"  > Загружаю {os.path.basename(filepath)} в Telegram...")
    bot = Bot(token=BOT_TOKEN)
    try:
        with open(filepath, 'rb') as video_file:
            message = await bot.send_video(chat_id=CHANNEL_ID, video=video_file, caption=title, read_timeout=300, write_timeout=300, connect_timeout=300)
        await bot.shutdown()
        print(f"  ✅ Успешно загружено.")
        return message.video.file_id
    except Exception as e:
        print(f"  ❌ Ошибка при загрузке в Telegram: {e}")
        await bot.shutdown()
        return None

# --- НОВАЯ ФУНКЦИЯ "АНТИСПОЙЛЕР" ---
def find_spoiler_time(video_filepath):
    """Извлекает кадры, распознает текст и ищет таймкод спойлеров с помощью Gemini."""
    print(f"  > [Антиспойлер] Анализирую первые {OCR_DURATION_SECONDS} секунд видео...")
    frames_folder = os.path.join(TEMP_FOLDER, 'frames')
    if not os.path.exists(frames_folder): os.makedirs(frames_folder)

    try:
        # 1. Извлекаем кадры (1 кадр в секунду)
        subprocess.run(
            ['ffmpeg', '-i', video_filepath, '-ss', '0', '-t', str(OCR_DURATION_SECONDS), '-vf', 'fps=1', os.path.join(frames_folder, 'frame-%03d.png')],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        
        # 2. Распознаем текст на всех кадрах
        all_text = ""
        frame_files = sorted([f for f in os.listdir(frames_folder) if f.endswith('.png')])
        for frame_file in frame_files:
            try:
                text = pytesseract.image_to_string(Image.open(os.path.join(frames_folder, frame_file)), lang='rus+eng')
                if text.strip():
                    all_text += text.strip().lower().replace('\n', ' ') + " | "
            except Exception:
                continue # Игнорируем ошибки на отдельных кадрах
        
        # Очищаем временные кадры
        for frame_file in frame_files:
            os.remove(os.path.join(frames_folder, frame_file))

        if not all_text.strip():
            print("  > [Антиспойлер] Текст на видео не найден.")
            return None

        # 3. Отправляем текст в Gemini для анализа
        if not GEMINI_API_KEY:
            print("  > [Антиспойлер] Ключ Gemini API не найден. Пропускаю анализ.")
            return None
            
        print("  > [Антиспойлер] Отправляю распознанный текст в Gemini...")
        model = genai.GenerativeModel('gemini-pro')
        prompt = (f"Проанализируй этот текст, распознанный с видеокадров. Найди таймкод окончания спойлеров. "
                    f"Ищи фразы вроде 'спойлеры до', 'без спойлеров с', 'таймкоды:'. "
                    f"Верни ТОЛЬКО таймкод в формате ММ:СС или ЧЧ:ММ:СС. Если таймкод не найден, верни слово 'null'.\n\n"
                    f"Текст:\n{all_text}")
        response = model.generate_content(prompt)
        
        match = re.search(r'(\d{1,2}:\d{2}(?::\d{2})?)', response.text)
        if match:
            spoiler_time = match.group(1)
            print(f"  > [Антиспойлер] Gemini нашел таймкод: {spoiler_time}")
            return spoiler_time
        else:
            print("  > [Антиспойлер] Gemini не нашел таймкод.")
            return None

    except Exception as e:
        print(f"  > [Антиспойлер] Произошла ошибка в процессе анализа: {e}")
        return None

async def process_video_async(video_id, title):
    print("-" * 50)
    print(f"🎬 Начинаю обработку видео: {title}")
    video_url = f"https://www.youtube.com/watch?v={video_id}"
    video_parts_info = []
    spoiler_end_time = None # <-- Новая переменная

    try:
        # --- ШАГ 1: СКАЧИВАНИЕ ---
        print("  [1/4] Скачиваю полное видео в 480p...")
        temp_filepath_template = os.path.join(TEMP_FOLDER, f'{video_id}_full.%(ext)s')
        command_dl = [
            'yt-dlp', '--cookies', COOKIE_FILE, '--user-agent', 'Mozilla/5.0 ...',
            '--no-check-certificate', '-f', 'best[height<=480]', 
            '--output', temp_filepath_template, video_url
        ]
        subprocess.run(command_dl, check=True, timeout=900)
        
        full_filename = next((f for f in os.listdir(TEMP_FOLDER) if f.startswith(f"{video_id}_full")), None)
        if not full_filename: return None
        
        full_filepath = os.path.join(TEMP_FOLDER, full_filename)
        print("  ✅ Файл скачан.")

        # --- ШАГ 2: АНАЛИЗ НА СПОЙЛЕРЫ (НОВЫЙ!) ---
        spoiler_end_time = find_spoiler_time(full_filepath)

        # --- ШАГ 3: НАРЕЗКА ---
        print("  [3/4] Начинаю нарезку...")
        chunk_filename_template = os.path.join(TEMP_FOLDER, f"{video_id}_part_%03d.mp4")
        subprocess.run(['ffmpeg', '-i', full_filepath, '-c', 'copy', '-map', '0', '-segment_time', str(CHUNK_DURATION_SECONDS), '-f', 'segment', '-reset_timestamps', '1', chunk_filename_template], check=True, timeout=900)
        os.remove(full_filepath)

        # --- ШАГ 4: ЗАГРУЗКА ---
        chunks = sorted([f for f in os.listdir(TEMP_FOLDER) if f.startswith(f"{video_id}_part_")])
        print(f"  ✅ Нарезано {len(chunks)} частей. [4/4] Загружаю в Telegram...")

        for i, chunk_filename in enumerate(chunks):
            chunk_filepath = os.path.join(TEMP_FOLDER, chunk_filename)
            part_title = f"{title} - Часть {i+1}"
            file_id = await upload_to_telegram(chunk_filepath, part_title)
            if file_id:
                video_parts_info.append({'part_num': i + 1, 'file_id': file_id})
            os.remove(chunk_filepath)
        
        if video_parts_info:
            print(f"🎉 Видео '{title}' полностью обработано!")
            # --- ИЗМЕНЕНИЕ: Добавляем таймкод в результат ---
            return {'id': video_id, 'title': title, 'parts': video_parts_info, 'spoiler_end_time': spoiler_end_time}
        
        return None
    except Exception as e:
        print(f"❌ КРИТИЧЕСКАЯ ОШИБКА для '{title}': {e}")
        return None

# --- Код main() и if __name__ == '__main__' остаются такими же, как в "красивой" версии ---
def main():
    if not os.path.exists(TEMP_FOLDER): os.makedirs(TEMP_FOLDER)
    if YOUTUBE_COOKIES_DATA:
        with open(COOKIE_FILE, 'w') as f: f.write(YOUTUBE_COOKIES_DATA)
        print("ℹ️ Временный файл cookies.txt создан.")
    else:
        print("⚠️ ВНИМАНИЕ: Секрет YOUTUBE_COOKIES не найден.")
    try:
        print("\n" + "="*50); print("🚀 Запуск проверки новых видео (GitHub Actions с Антиспойлером)"); print("="*50 + "\n")
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
