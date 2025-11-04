
import yt_dlp
from pyrogram import Client, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import config
import os
import asyncio
import re
import time
from modules.database import DataBase
import subprocess
from PIL import Image
import glob

db = DataBase()

def clear_downloads():
    fs = os.listdir('downloads')
    for f in fs:
        delete_file(f"downloads/{f}")

def crop_to_square(image_path, out_path):
    with Image.open(image_path) as img:
        width, height = img.size
        new_size = min(width, height)
        left = (width - new_size) / 2
        top = (height - new_size) / 2
        right = (width + new_size) / 2
        bottom = (height + new_size) / 2
        img = img.crop((left, top, right, bottom))
        img.save(out_path)

def sanitize_filename(text):
    if not text:
        return ""
    sanitized_text = text.strip()
    sanitized_text = re.sub(r'[\\/:"*?<>|]+', '', sanitized_text)
    sanitized_text = re.sub(r'%+', '', sanitized_text)
    sanitized_text = re.sub(r'[\x00-\x1f]', '', sanitized_text)
    sanitized_text = re.sub(r'\s+', '_', sanitized_text)
    sanitized_text = re.sub(r'_+', '_', sanitized_text)
    return sanitized_text[:200]

def download_audio(video_url, output_path, chat_id, thumb, bot_username, payment_payload=None, user_id_for_work=None, session_id=None):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        if not output_path.lower().endswith('.mp3'):
            output_path = output_path + '.mp3'

        base_name = os.path.basename(output_path)
        base_no_ext = os.path.splitext(base_name)[0]
        safe_base = sanitize_filename(base_no_ext)
        outtmpl = os.path.join('downloads', safe_base)

        ydl_opts = {
            'format': 'bestaudio/best',
            'postprocessors': [
                {'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '128'},
                {'key': 'FFmpegMetadata'},
                {'key': 'EmbedThumbnail'}
            ],
            'outtmpl': outtmpl,
            'writethumbnail': True,
            'keepvideo': False,
        }
        ydl_opts['cookiefile'] = 'cookies/youtube.txt'

        max_retries = 10
        last_exc = None
        for attempt in range(1, max_retries + 1):
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                try:
                    ydl.download([video_url])
                    last_exc = None
                    break
                except yt_dlp.utils.DownloadError as de:
                    last_exc = de
                    time.sleep(4)
                    continue

        if last_exc is not None:
            try:
                session_base = f"sessions/{(session_id if session_id is not None else chat_id)}"
                app = Client(session_base, bot_token=config.bot_token, api_id=config.api_id, api_hash=config.api_hash)
                app.start()
                msg = str(last_exc)
                kb = None
                if payment_payload:
                    pay_price = config.stars_premium_price if (":prem" in str(payment_payload)) else config.stars_price
                    kb = InlineKeyboardMarkup(
                        [[InlineKeyboardButton(f"🔄 Refund {pay_price}⭐", callback_data=f"refund:{payment_payload}")]]
                    )
                app.send_message(chat_id=chat_id, text=f"Download failed after {max_retries} attempts: {msg}", reply_markup=kb)
                app.stop()
                delete_pyrogram_session_files(session_base)
            except Exception:
                pass
            db.set_work(user_id_for_work or chat_id, 0)
            delete_file(output_path)
            return

        possible_thumb = None
        for ext in ('.jpg', '.jpeg', '.webp', '.png'):
            candidate = outtmpl + ext
            if os.path.exists(candidate):
                possible_thumb = candidate
                break

        audio_thumb = None
        if possible_thumb:
            audio_thumb = outtmpl + '_audio.jpg'
            try:
                crop_to_square(possible_thumb, audio_thumb)
            except Exception:
                audio_thumb = possible_thumb
        else:
            if thumb and os.path.exists(thumb):
                audio_thumb = outtmpl + '_audio.jpg'
                try:
                    crop_to_square(thumb, audio_thumb)
                except Exception:
                    audio_thumb = thumb

        session_base = f"sessions/{(session_id if session_id is not None else chat_id)}"
        app = Client(session_base, bot_token=config.bot_token, api_id=config.api_id, api_hash=config.api_hash)
        app.start()
        produced_mp3 = outtmpl + '.mp3'
        try:
            app.send_audio(chat_id=chat_id, audio=produced_mp3, thumb=audio_thumb, title=safe_base, caption=f"💎 <b><a href='https://t.me/{bot_username}'>@{bot_username}</a></b>", parse_mode=enums.ParseMode.HTML)
        except:
            try:
                app.send_audio(chat_id=chat_id, audio=output_path, title=safe_base, caption=f"💎 <b><a href='https://t.me/{bot_username}'>@{bot_username}</a></b>", parse_mode=enums.ParseMode.HTML)
            except Exception as e:
                print(f"Failed to send audio: {e}")
        app.stop()
        delete_pyrogram_session_files(session_base)
        if audio_thumb:
            delete_file(audio_thumb)
        delete_file(produced_mp3)
    except Exception as e:
        try:
            session_base = f"sessions/{(session_id if session_id is not None else chat_id)}"
            app = Client(session_base, bot_token=config.bot_token, api_id=config.api_id, api_hash=config.api_hash)
            app.start()
            kb = None
            if payment_payload:
                pay_price = config.stars_premium_price if (":prem" in str(payment_payload)) else config.stars_price
                kb = InlineKeyboardMarkup(
                    [[InlineKeyboardButton(f"🔄 Refund {pay_price}⭐", callback_data=f"refund:{payment_payload}")]]
                )
            app.send_message(chat_id=chat_id, text=f"Download error: {e}", reply_markup=kb)
            app.stop()
            delete_pyrogram_session_files(session_base)
        except Exception:
            pass
    db.set_work(user_id_for_work or chat_id, 0)
    delete_file(output_path)

def get_video_formats(url, domain):
    ydl_opts = {
        'listformats': True,
        'cookiefile': 'cookies/insta.txt'
    }
    if domain.startswith("youtu"):
        ydl_opts['cookiefile'] = 'cookies/youtube.txt'
    
    if domain == 'instagram.com':
        ydl_opts['cookiefile'] = 'cookies/insta.txt'

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info_dict = ydl.extract_info(url, download=False)
        return info_dict

def is_youtube_public(url: str) -> bool:
    try:
        with yt_dlp.YoutubeDL({'listformats': True}) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception:
        return False
    fmts = info.get('formats', []) if isinstance(info, dict) else []
    for f in fmts:
        try:
            if f.get('vcodec') == 'images':
                continue

            proto = (f.get('protocol') or '').lower()
            if not proto:
                continue

            if proto in ('m3u8', 'm3u8_native', 'https', 'http', 'dash', 'http_dash_segments'):
                ext = (f.get('ext') or '').lower()
                if ext in ('mp4', 'webm', 'm4a'):
                    return True
        except Exception:
            continue
    return False

def get_domain(url):
    domain_pattern = r'^(https?:\/\/)?(www\.)?([a-zA-Z0-9.-]+\.[a-zA-Z]{2,})(\/.*)?$'
    match = re.match(domain_pattern, url)
    if match:
        return match.group(3)
    return None

def delete_file(f_path):
    for _ in range(5):
        try:
            os.remove(f_path)
            break
        except:
            pass
        time.sleep(5)


def ensure_jpg_thumb(thumb_path, video_path, out_base):
    if not out_base:
        return None
    target = os.path.join('downloads', f"{out_base}_thumb.jpg")
    try:
        if thumb_path and os.path.exists(thumb_path):
            try:
                with Image.open(thumb_path) as im:
                    rgb = im.convert('RGB')
                    max_w = 1280
                    if rgb.width > max_w:
                        h = int(max_w * rgb.height / rgb.width)
                        rgb = rgb.resize((max_w, h), Image.LANCZOS)
                    rgb.save(target, format='JPEG', quality=85)
                    return target
            except Exception:
                pass

        if video_path and os.path.exists(video_path):
            try:
                cmd = [
                    'ffmpeg', '-y', '-i', video_path,
                    '-ss', '00:00:01', '-vframes', '1',
                    '-q:v', '2', target
                ]
                subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if os.path.exists(target):
                    try:
                        with Image.open(target) as im2:
                            im2.convert('RGB')
                        return target
                    except Exception:
                        delete_file(target)
            except Exception:
                pass
    except Exception:
        pass
    return None

def simple_downloader(url, output_path, chat_id, domain, video_format=None, title_orig="", thumb=None, payment_payload=None, user_id_for_work=None, session_id=None):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        ydl_opts = {'format': 'best', 'outtmpl': output_path}
        if domain == "instagram.com":
            ydl_opts['cookiefile'] = 'cookies/insta.txt'
            ydl_opts['quiet'] = True
        elif domain.startswith("youtu"):
            try:
                with yt_dlp.YoutubeDL({'cookiefile': 'cookies/youtube.txt'}) as probe:
                    info = probe.extract_info(url, download=False)
            except Exception:
                info = {}
            fmts = info.get('formats', []) if isinstance(info, dict) else []
            selected = None
            for f in fmts:
                if str(f.get('format_id')) == str(video_format):
                    selected = f
                    break
            if selected and selected.get('acodec') and selected.get('acodec') != 'none':
                ydl_opts['format'] = str(video_format)
            else:
                ydl_opts['format'] = f"{video_format}+bestaudio/best"
                ydl_opts['merge_output_format'] = "mp4"

        ydl_opts['cookiefile'] = 'cookies/youtube.txt'
        max_retries = 10
        last_exc = None
        for attempt in range(1, max_retries + 1):
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                try:
                    ydl.download([url])
                    last_exc = None
                    break
                except yt_dlp.utils.DownloadError as de:
                    last_exc = de
                    time.sleep(4)
                    continue

        if last_exc is not None:
            try:
                session_base = f"sessions/{(session_id if session_id is not None else chat_id)}"
                app = Client(session_base, bot_token=config.bot_token, api_id=config.api_id, api_hash=config.api_hash)
                app.start()
                msg = str(last_exc)
                kb = None
                if payment_payload:
                    pay_price = config.stars_premium_price if (":prem" in str(payment_payload)) else config.stars_price
                    kb = InlineKeyboardMarkup(
                        [[InlineKeyboardButton(f"🔄 Refund {pay_price}⭐", callback_data=f"refund:{payment_payload}")]]
                    )
                app.send_message(chat_id=chat_id, text=f"Download failed after {max_retries} attempts: {msg}", reply_markup=kb)
                app.stop()
                delete_pyrogram_session_files(session_base)
            except Exception:
                pass
            db.set_work(user_id_for_work or chat_id, 0)
            delete_file(output_path)
            return

        try:
            with yt_dlp.YoutubeDL({'cookiefile': 'cookies/youtube.txt'}) as ydl_info:
                info_dict = ydl_info.extract_info(url, download=False)
        except Exception:
            info_dict = {}

        width = 0
        height = 0
        fs = info_dict.get('formats', [])
        for f in fs:
            if f.get('format_id') == video_format:
                width = f.get('width', 0) or 0
                height = f.get('height', 0) or 0

        session_base = f"sessions/{(session_id if session_id is not None else chat_id)}"
        app = Client(session_base, bot_token=config.bot_token, api_id=config.api_id, api_hash=config.api_hash)
        app.start()
        base_name = os.path.splitext(os.path.basename(output_path))[0]
        safe_base = sanitize_filename(base_name)
        norm_thumb = ensure_jpg_thumb(thumb, output_path, safe_base)
        if norm_thumb:
            app.send_video(chat_id=chat_id, video=output_path, caption=title_orig, thumb=norm_thumb, width=width, height=height)
        else:
            app.send_video(chat_id=chat_id, video=output_path, caption=title_orig, thumb=thumb, width=width, height=height)
        app.stop()
        delete_pyrogram_session_files(session_base)
    except Exception as e:
        try:
            if domain in ["instagram.com", "twitter.com", "x.com"]:
                output_path = output_path.replace("mp4", "jpg")
                command = [
                    "gallery-dl",
                    "--config", "gallery-dl.conf",
                    "--filename", output_path.replace("downloads/", ""),
                    "--directory", "downloads/",
                    url
                ]
                subprocess.run(command)
                session_base = f"sessions/{(session_id if session_id is not None else chat_id)}"
                app = Client(session_base, bot_token=config.bot_token, api_id=config.api_id, api_hash=config.api_hash)
                app.start()
                app.send_photo(chat_id=chat_id, photo=output_path)
                app.stop()
                delete_pyrogram_session_files(session_base)
        except Exception:
            try:
                session_base = f"sessions/{(session_id if session_id is not None else chat_id)}"
                app = Client(session_base, bot_token=config.bot_token, api_id=config.api_id, api_hash=config.api_hash)
                app.start()
                kb = None
                if payment_payload:
                    pay_price = config.stars_premium_price if (":prem" in str(payment_payload)) else config.stars_price
                    kb = InlineKeyboardMarkup(
                        [[InlineKeyboardButton(f"🔄 Refund {pay_price}⭐", callback_data=f"refund:{payment_payload}")]]
                    )
                app.send_message(chat_id=chat_id, text=f"Download error: {e}", reply_markup=kb)
                app.stop()
                delete_pyrogram_session_files(session_base)
            except Exception:
                pass
    db.set_work(user_id_for_work or chat_id, 0)
    delete_file(output_path)

def delete_pyrogram_session_files(session_base: str):
    # Intentionally disabled: keep Pyrogram sessions persistent per chat_id as requested
    return
