
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
import json
import requests

db = DataBase()

def bot_api_send_message(chat_id: int | str, text: str, payment_payload: str | None = None) -> bool:
    try:
        url = f"https://api.telegram.org/bot{config.bot_token}/sendMessage"
        data = {
            'chat_id': str(chat_id),
            'text': text,
            'disable_web_page_preview': 'true'
        }
        if payment_payload:
            # Build inline keyboard with refund button
            pay_price = config.stars_premium_price if (":prem" in str(payment_payload)) else config.stars_price
            kb = {
                "inline_keyboard": [[{"text": f"🔄 Refund {pay_price}⭐", "callback_data": f"refund:{payment_payload}"}]]
            }
            data['reply_markup'] = json.dumps(kb)
        resp = requests.post(url, data=data, timeout=30)
        if resp.status_code != 200:
            return False
        j = resp.json()
        return bool(j.get('ok'))
    except Exception:
        return False

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
            'cookiefile': 'cookies/youtube.txt',
            'noprogress': True,
            'quiet': True,
            'extractor_args': {
                'youtube': {
                    'player_client': ['android', 'ios', 'web']
                }
            }
        }

        max_retries = 3
        last_exc = None
        for attempt in range(1, max_retries + 1):
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                try:
                    ydl.download([video_url])
                    last_exc = None
                    break
                except yt_dlp.utils.DownloadError as de:
                    last_exc = de
                    time.sleep(min(2 * attempt, 6))
                    continue

        if last_exc is not None:
            # Prefer Bot API notification so we don't hit Pyrogram peer issues
            msg = str(last_exc)
            sent = bot_api_send_message(chat_id, f"Download failed after {max_retries} attempts: {msg}", payment_payload)
            if not sent:
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

        produced_mp3 = outtmpl + '.mp3'
        # Try Bot API upload for files <= 50MB, else fallback to Pyrogram
        try:
            size_bytes = os.path.getsize(produced_mp3)
        except Exception:
            size_bytes = 0

        if size_bytes and size_bytes <= 50 * 1024 * 1024:
            try:
                url = f"https://api.telegram.org/bot{config.bot_token}/sendAudio"
                data = {
                    'chat_id': str(chat_id),
                    'caption': f"💎 <b><a href='https://t.me/{bot_username}'>@{bot_username}</a></b>",
                    'parse_mode': 'HTML',
                    'title': safe_base,
                }
                with open(produced_mp3, 'rb') as af:
                    files = {
                        'audio': (os.path.basename(produced_mp3), af)
                    }
                    if audio_thumb and os.path.exists(audio_thumb):
                        with open(audio_thumb, 'rb') as tf:
                            files['thumbnail'] = (os.path.basename(audio_thumb), tf)
                            resp = requests.post(url, data=data, files=files, timeout=120)
                    else:
                        resp = requests.post(url, data=data, files=files, timeout=120)
                if resp.status_code != 200 or not resp.json().get('ok', False):
                    raise RuntimeError(f"Bot API sendAudio failed: HTTP {resp.status_code} {resp.text[:200]}")
            except Exception as e:
                # Fallback to Pyrogram if Bot API fails
                try:
                    session_base = f"sessions/{(session_id if session_id is not None else chat_id)}"
                    app = Client(session_base, bot_token=config.bot_token, api_id=config.api_id, api_hash=config.api_hash)
                    app.start()
                    app.send_audio(chat_id=chat_id, audio=produced_mp3, thumb=audio_thumb, title=safe_base, caption=f"💎 <b><a href='https://t.me/{bot_username}'>@{bot_username}</a></b>", parse_mode=enums.ParseMode.HTML)
                    app.stop()
                    delete_pyrogram_session_files(session_base)
                except Exception as e2:
                    print(f"Failed to send audio (both Bot API and Pyrogram): {e} | {e2}")
                    # Inform user and offer refund
                    bot_api_send_message(chat_id, f"Send failed: {e2}", payment_payload)
                    db.set_work(user_id_for_work or chat_id, 0)
                    try:
                        delete_file(output_path)
                    except Exception:
                        pass
                    return
        else:
            session_base = f"sessions/{(session_id if session_id is not None else chat_id)}"
            app = Client(session_base, bot_token=config.bot_token, api_id=config.api_id, api_hash=config.api_hash)
            app.start()
            try:
                app.send_audio(chat_id=chat_id, audio=produced_mp3, thumb=audio_thumb, title=safe_base, caption=f"💎 <b><a href='https://t.me/{bot_username}'>@{bot_username}</a></b>", parse_mode=enums.ParseMode.HTML)
            except Exception as e:
                print(f"Failed to send audio: {e}")
                bot_api_send_message(chat_id, f"Send failed: {e}", payment_payload)
                app.stop()
                delete_pyrogram_session_files(session_base)
                db.set_work(user_id_for_work or chat_id, 0)
                try:
                    delete_file(output_path)
                except Exception:
                    pass
                return
            app.stop()
            delete_pyrogram_session_files(session_base)
        if audio_thumb and os.path.exists(audio_thumb) and (audio_thumb != produced_mp3):
            try:
                delete_file(audio_thumb)
            except Exception:
                pass
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
    try:
        delete_file(output_path)
    except Exception:
        pass

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
        ydl_opts = {
            'format': 'best',
            'outtmpl': output_path,
            'noprogress': True,
            'quiet': True
        }
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
            ydl_opts['extractor_args'] = {
                'youtube': {
                    'player_client': ['android', 'ios', 'web']
                }
            }
        max_retries = 3
        last_exc = None
        for attempt in range(1, max_retries + 1):
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                try:
                    ydl.download([url])
                    last_exc = None
                    break
                except yt_dlp.utils.DownloadError as de:
                    last_exc = de
                    time.sleep(min(2 * attempt, 6))
                    continue

        if last_exc is not None:
            msg = str(last_exc)
            sent = bot_api_send_message(chat_id, f"Download failed after {max_retries} attempts: {msg}", payment_payload)
            if not sent:
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
                    app.send_message(chat_id=chat_id, text=f"Download failed after {max_retries} attempts: {msg}", reply_markup=kb)
                    app.stop()
                    delete_pyrogram_session_files(session_base)
                except Exception:
                    pass
            db.set_work(user_id_for_work or chat_id, 0)
            delete_file(output_path)
            return

        try:
            with yt_dlp.YoutubeDL({'cookiefile': 'cookies/youtube.txt', 'quiet': True}) as ydl_info:
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

        base_name = os.path.splitext(os.path.basename(output_path))[0]
        safe_base = sanitize_filename(base_name)
        norm_thumb = ensure_jpg_thumb(thumb, output_path, safe_base)

        # Try Bot API upload for files <= 50MB, else fallback to Pyrogram
        try:
            size_bytes = os.path.getsize(output_path)
        except Exception:
            size_bytes = 0

        if size_bytes and size_bytes <= 50 * 1024 * 1024:
            try:
                url = f"https://api.telegram.org/bot{config.bot_token}/sendVideo"
                data = {
                    'chat_id': str(chat_id),
                    'caption': title_orig or safe_base,
                    'supports_streaming': 'true',
                }
                with open(output_path, 'rb') as vf:
                    files = {
                        'video': (os.path.basename(output_path), vf)
                    }
                    if norm_thumb and os.path.exists(norm_thumb):
                        with open(norm_thumb, 'rb') as tf:
                            files['thumbnail'] = (os.path.basename(norm_thumb), tf)
                            resp = requests.post(url, data=data, files=files, timeout=120)
                    else:
                        resp = requests.post(url, data=data, files=files, timeout=120)
                if resp.status_code != 200 or not resp.json().get('ok', False):
                    raise RuntimeError(f"Bot API sendVideo failed: HTTP {resp.status_code} {resp.text[:200]}")
            except Exception as e:
                # Fallback to Pyrogram if Bot API fails
                try:
                    session_base = f"sessions/{(session_id if session_id is not None else chat_id)}"
                    app = Client(session_base, bot_token=config.bot_token, api_id=config.api_id, api_hash=config.api_hash)
                    app.start()
                    if norm_thumb and os.path.exists(norm_thumb):
                        app.send_video(chat_id=chat_id, video=output_path, caption=title_orig, thumb=norm_thumb, width=width, height=height)
                    else:
                        app.send_video(chat_id=chat_id, video=output_path, caption=title_orig, thumb=thumb, width=width, height=height)
                    app.stop()
                    delete_pyrogram_session_files(session_base)
                except Exception as e2:
                    print(f"Failed to send video (both Bot API and Pyrogram): {e} | {e2}")
                    bot_api_send_message(chat_id, f"Send failed: {e2}", payment_payload)
                    db.set_work(user_id_for_work or chat_id, 0)
                    try:
                        delete_file(output_path)
                    except Exception:
                        pass
                    return
        else:
            session_base = f"sessions/{(session_id if session_id is not None else chat_id)}"
            app = Client(session_base, bot_token=config.bot_token, api_id=config.api_id, api_hash=config.api_hash)
            app.start()
            try:
                if norm_thumb and os.path.exists(norm_thumb):
                    app.send_video(chat_id=chat_id, video=output_path, caption=title_orig, thumb=norm_thumb, width=width, height=height)
                else:
                    app.send_video(chat_id=chat_id, video=output_path, caption=title_orig, thumb=thumb, width=width, height=height)
            except Exception as e:
                print(f"Failed to send video: {e}")
                bot_api_send_message(chat_id, f"Send failed: {e}", payment_payload)
                app.stop()
                delete_pyrogram_session_files(session_base)
                db.set_work(user_id_for_work or chat_id, 0)
                try:
                    delete_file(output_path)
                except Exception:
                    pass
                return
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
                # Try Bot API sendPhoto first
                try:
                    api = f"https://api.telegram.org/bot{config.bot_token}/sendPhoto"
                    with open(output_path, 'rb') as pf:
                        files = {'photo': (os.path.basename(output_path), pf)}
                        data = {'chat_id': str(chat_id)}
                        r = requests.post(api, data=data, files=files, timeout=120)
                        ok = (r.status_code == 200 and r.json().get('ok'))
                    if not ok:
                        raise RuntimeError(r.text[:200])
                except Exception:
                    session_base = f"sessions/{(session_id if session_id is not None else chat_id)}"
                    app = Client(session_base, bot_token=config.bot_token, api_id=config.api_id, api_hash=config.api_hash)
                    app.start()
                    app.send_photo(chat_id=chat_id, photo=output_path)
                    app.stop()
                    delete_pyrogram_session_files(session_base)
        except Exception:
            # Prefer Bot API error notification
            bot_api_send_message(chat_id, f"Download error: {e}", payment_payload)
    db.set_work(user_id_for_work or chat_id, 0)
    delete_file(output_path)

def delete_pyrogram_session_files(session_base: str):
    return
