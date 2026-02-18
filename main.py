import asyncio
from aiogram import Bot, Dispatcher, F, types
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message, CallbackQuery, InlineQuery
from aiogram.types import InlineQueryResultArticle, InputTextMessageContent, InlineQueryResultPhoto, FSInputFile
from aiogram.types import InlineKeyboardMarkup as AioInlineKeyboardMarkup, InlineKeyboardButton as AioInlineKeyboardButton
from aiogram.types import PreCheckoutQuery, LabeledPrice
import aiohttp
from aiogram.filters.command import Command
from modules.database import DataBase
from modules.keyboards import *
from modules.state import *
from aiogram.client.bot import DefaultBotProperties
from aiogram.enums.parse_mode import ParseMode
from config import *
from modules.middleware.exists_user import ExistsUserMiddleware
from modules.middleware.throttling import ThrottlingMiddleware
from aiogram.fsm.context import FSMContext
from downloader import *
from downloader import generate_download_id, cancel_download_process, send_download_started_message, update_download_message
import threading
import traceback
import requests
import random
import config
import string
import json
import os
import html
from modules import dlp_manager
from modules import scheduler
from modules.keyboards import ban_kb

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}

# Telegram can reject thumbnails that are not a real photo (e.g. HTML),
# or images in formats like AVIF/WEBP returned by some CDNs.
# Normalize to a safe JPEG before uploading.
def normalize_thumbnail_to_jpeg(path: str, *, max_side: int = 1280) -> bool:
    try:
        if not path or not os.path.exists(path):
            return False
        if os.path.getsize(path) <= 0:
            return False

        from PIL import Image

        # Verify file is an image
        with Image.open(path) as im:
            im.verify()

        # Re-open for actual processing
        with Image.open(path) as im:
            fmt = (im.format or "").upper()
            w, h = im.size
            if max(w, h) > max_side:
                im.thumbnail((max_side, max_side))

            # Convert unsupported/odd formats to JPEG
            if fmt not in {"JPEG", "JPG", "PNG"}:
                im = im.convert("RGB")
                im.save(path, format="JPEG", quality=90, optimize=True)
            else:
                # Also ensure mode is compatible
                if im.mode not in ("RGB", "L"):
                    im = im.convert("RGB")
                # If PNG, keep as-is; Telegram accepts it. If something still fails,
                # the upload path will log the error and fall back to URL/text.
                if fmt in {"JPEG", "JPG"}:
                    im.save(path, format="JPEG", quality=90, optimize=True)

        return os.path.exists(path) and os.path.getsize(path) > 0
    except Exception as e:
        try:
            print(f"normalize_thumbnail_to_jpeg failed: {e!r} path={path}")
        except Exception:
            pass
        return False

def looks_like_image_url(url: str | None) -> bool:
    if not url or not isinstance(url, str):
        return False
    u = url.lower().split("?", 1)[0]
    return any(u.endswith(ext) for ext in IMAGE_EXTS)

def is_supported_domain(domain: str | None) -> bool:
    if not domain:
        return False
    d = domain.lower()
    if ("youtu" in d) or ("soundcloud.com" in d):
        return True
    if ("tiktok" in d) or ("instagram" in d) or ("pinterest" in d):
        return True
    if ("x.com" in d) or ("twitter.com" in d):
        return True
    if (d == "vk.com") or ("vkvideo.ru" in d):
        return True
    return False

def is_youtube_playlist_like(url: str) -> bool:
    """Detect YouTube watch/mix/playlist style links we should ignore.
    We ignore if there's a 'list=' param (playlist or mix) to avoid parsing huge playlist.
    """
    try:
        if 'youtu' not in url:
            return False
        from urllib.parse import urlparse, parse_qs
        u = urlparse(url)
        if u.netloc not in {"www.youtube.com", "youtube.com", "m.youtube.com", "music.youtube.com"}:
            return False
        if u.path not in ("/watch", "/playlist"):
            return False
        qs = parse_qs(u.query or '')
        if 'list' in qs:
            # list param present -> treat as playlist/mix
            return True
        return False
    except Exception:
        return False

db = DataBase()
start_msg = (
    "<tg-emoji emoji-id=\"5373230968943420212\">⭐</tg-emoji> Good Day!\n"
    "This is an <a href=\"https://github.com/mercury-devel/telegram_ytdlp\">open-source video downloader</a> on telegram\n"
    "This bot can download:\n\n"
    "Photos and videos from Instagram and TikTok.\n"
    "Videos (with quality selection) and audio (in the best quality) from YouTube.\n"
    "Music from SoundCloud.\n"
)

async def send_start_message(message: Message):
    kb = remove_kb()
    photo_url = getattr(config, 'start_photo_url', None)
    if photo_url:
        try:
            await message.answer_photo(photo=photo_url, caption=start_msg[:1024], reply_markup=kb)
            return
        except Exception as e:
            try:
                print(f"Failed to send start photo: {e!r}; url={photo_url}")
            except Exception:
                pass
    await message.answer(start_msg, reply_markup=kb, disable_web_page_preview=True)

async def welcome(message: Message, state: FSMContext):
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) > 1 and parts[1].startswith("dl_"):
        token = parts[1] 
        link = db.get_deeplink(token)
        if not link:
            await message.answer("Link is invalid or expired.")
            return
        db.delete_deeplink(token)
        await process_link_message(message, state, link)
        return
    await send_start_message(message)

async def youtube_download(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    _, work = db.get_user(call.from_user.id)
    if work == 1:
        if call.message:
            await call.message.answer("Wait while your video is downloading")
        else:
            await call.answer("Wait while your video is downloading")
        return

    parts = call.data.split(":")
    _, format, size = parts[:3]
    note = parts[3] if len(parts) > 3 else None
    token = None
    if len(parts) > 4 and parts[-1].startswith("il_"):
        token = parts[-1]

    if not data:
        context = None
        if token:
            try:
                raw = db.get_deeplink(token)
                if raw:
                    context = json.loads(raw)
                    try:
                        db.delete_deeplink(token)
                    except Exception:
                        pass
            except Exception:
                context = None
        if not context:
            await call.answer("Send me link again", show_alert=True)
            return
        link = context.get('link')
        domain = context.get('domain')
        
        # Получаем информацию о видео через yt-dlp для точного названия
        try:
            info_dict, _ = get_video_formats(link, domain)
            video_title = info_dict.get('title', 'Video')
            # Получаем thumbnail URL для сохранения
            thumb_url = info_dict.get('thumbnail')
            if not looks_like_image_url(thumb_url):
                for th in (info_dict.get('thumbnails') or []):
                    u = th.get('url') if isinstance(th, dict) else None
                    if looks_like_image_url(u):
                        thumb_url = u
                        break
        except Exception:
            video_title = context.get('title', 'Video')
            thumb_url = context.get('thumbnail_url')
        
        title = sanitize_filename(video_title)
        random_name = random.randint(10000, 99999)
        video_path = f"downloads/{random_name}.mp4"
        thumbnail_path = video_path.replace("mp4", "jpg")
        
        # Сохраняем thumbnail если есть URL
        if thumb_url and looks_like_image_url(thumb_url):
            try:
                response = requests.get(
                    thumb_url,
                    timeout=10,
                    headers={
                        "User-Agent": "Mozilla/5.0",
                        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
                    },
                )
                ctype = (response.headers.get("Content-Type") or "").lower()
                if response.ok and response.content and ("image/" in ctype or not ctype):
                    with open(thumbnail_path, 'wb') as file:
                        file.write(response.content)
                    normalize_thumbnail_to_jpeg(thumbnail_path)
            except Exception:
                pass
        
        try:
            public_ok = is_youtube_public(link) if domain and 'youtu' in domain else True
        except Exception:
            public_ok = True
        premium_mode = False if public_ok else True
    else:
        link = data['link']
        domain = data['domain']
        video_path = data['video_path']
        thumbnail_path = data['thumbnail_path']
        title = sanitize_filename(data['title'])
        premium_mode = bool(data.get('premium'))
    max_size = 2 * 1024 * 1024 * 1024
    if int(size) >= max_size:
        if call.message:
            await call.message.answer("File is too large. Try another")
        else:
            await call.answer("File is too large. Try another")
        return 
    # Free mode: no whitelist or stars required
    requires_payment = False

    if call.message:
        try:
            await call.message.delete()
        except Exception:
            pass

    # No payments in free mode; proceed to download immediately

    db.set_work(call.from_user.id, 1)
    target_chat_id = call.message.chat.id if call.message else call.from_user.id

    sess_id = None
    if call.message:
        sess_id = str(call.message.chat.id)
    else:
        inl = getattr(call, 'inline_message_id', None)
        if inl:
            sess_id = f"inline_{inl}"
        else:
            sess_id = str(target_chat_id)

    # Генерируем уникальный ID для загрузки
    download_id = generate_download_id(call.from_user.id)
    
    # Отправляем сообщение о начале загрузки с кнопкой отмены
    # Только если не в inline-режиме (в inline нельзя отправлять сообщения)
    message_id = None
    if call.message:
        try:
            message_id = send_download_started_message(target_chat_id, download_id, link)
        except Exception as e:
            print(f"Failed to send download started message: {e}")
    else:
        # In inline mode just answer callback
        try:
            await call.answer("Download started...")
        except:
            pass

    # Сохраняем информацию о загрузке в БД
    db.add_active_download(
        download_id=download_id,
        user_id=call.from_user.id,
        chat_id=target_chat_id,
        url=link,
        format_id=format if format != "audio" else "audio",
        file_path=video_path if format != "audio" else f"downloads/{title}.mp3",
        message_id=message_id
    )

    if format != "audio":
        title_for_send = title  # Используем title из context или data
        # Используем новую функцию с поддержкой отмены
        my_thread = threading.Thread(
            target=simple_downloader_with_cancel, 
            args=(link, video_path, target_chat_id, domain, format, title_for_send, 
                  thumbnail_path, call.from_user.id, sess_id, download_id)
        )
        my_thread.start()
    else:
        audio_path = f"downloads/{title}.mp3"
        bot_info = await call.bot.get_me()
        bot_username = bot_info.username
        # Используем новую функцию с поддержкой отмены
        my_thread = threading.Thread(
            target=download_audio_with_cancel, 
            args=(link, audio_path, target_chat_id, thumbnail_path, bot_username, 
                  call.from_user.id, sess_id, download_id)
        )
        my_thread.start()

async def process_link_message(message: Message, state: FSMContext, link: str):
    try:
        domain = get_domain(link)
        if domain:
            # Ignore YouTube playlist/mix links with list= to prevent heavy playlist parsing
            if domain and 'youtu' in domain and is_youtube_playlist_like(link):
                await message.answer("Please send a direct video link without the list= parameter (playlists are ignored).")
                return
            if domain == "vk.com":
                if link.find("vk.com/video") == -1 and link.find("vk.com/clip") == -1:
                    return
                if link.find("@") > -1:
                    return
            elif domain == "vkvideo.ru":
                if link.find("@") > -1:
                    return
                link = link.replace("vkvideo.ru", "vk.com")
                domain = "vk.com"
            _, work = db.get_user(message.from_user.id)
            if work == 1:
                await message.answer("Wait while your video is downloading")
                return
            random_name = random.randint(10000, 99999)
            video_path = f"downloads/{random_name}.mp4"
            # Log the incoming link to log chat if configured, unless user is whitelisted from logging
            if config.log_chat:
                try:
                    u = message.from_user
                    uid = str(u.id)
                    _skip = {s.strip() for s in getattr(config, 'no_log_whitelist', []) if s.strip()}
                    if uid not in _skip:
                        mention = f"<a href='tg://user?id={u.id}'>{u.first_name}</a>"
                        await message.bot.send_message(chat_id=config.log_chat,
                                                        text=f"<code>{u.id}</code> {mention} sent:\n{link}",
                                                        reply_markup=ban_kb(u.id))
                except Exception as e:
                    print(f"Log send error: {e}")
            info_dict, ytlog = get_video_formats(link, domain)
            live = info_dict.get('is_live', False)
            if live:
                await message.answer("Live streams are restricted!")
                return
            title_orig = info_dict.get('title', 'No name')

            if domain.find("soundcloud.com") > -1:
                # Сообщение о начале загрузки будет отправлено через send_download_started_message
                title = sanitize_filename(title_orig)
                audio_path = f"downloads/{title}.mp3"
                # Robust thumbnail picking: iterate available entries and pick first valid URL
                thumb = None
                th_list = info_dict.get('thumbnails') or []
                if isinstance(th_list, list):
                    for th in th_list:
                        u = th.get('url') if isinstance(th, dict) else None
                        if looks_like_image_url(u):
                            thumb = u
                            break
                    if not thumb and th_list:
                        # Fallback: try last entry if any
                        last = th_list[-1]
                        thumb = last.get('url') if isinstance(last, dict) else None
                thumbnail_path = video_path.replace("mp4", "jpg")
                if thumb:
                    try:
                        response = requests.get(thumb, timeout=10)
                        if response.ok and response.content:
                            with open(thumbnail_path, 'wb') as file:
                                file.write(response.content)
                    except Exception:
                        pass
                bot_info = await message.bot.get_me()
                bot_username = bot_info.username

                # Генерируем ID для загрузки
                download_id = generate_download_id(message.from_user.id)
                
                # Отправляем сообщение о начале загрузки
                message_id = send_download_started_message(message.chat.id, download_id, link)

                # Сохраняем информацию о загрузке в БД
                db.add_active_download(
                    download_id=download_id,
                    user_id=message.from_user.id,
                    chat_id=message.chat.id,
                    url=link,
                    format_id="audio",
                    file_path=audio_path,
                    message_id=message_id
                )
                my_thread = threading.Thread(target=download_audio_with_cancel, args=(link, audio_path, message.chat.id, thumbnail_path, bot_username, message.from_user.id, str(message.chat.id), download_id))
                my_thread.start()
                return
            elif domain.find("youtu") > -1:
                formats = info_dict.get('formats', [])
                live = info_dict.get('is_live', False)
                if live:
                    await message.answer("Live streams are restricted!")
                    return

                thumbnail_url = info_dict.get('thumbnail')
                if not looks_like_image_url(thumbnail_url):
                    for th in (info_dict.get('thumbnails') or []):
                        u = th.get('url') if isinstance(th, dict) else None
                        if looks_like_image_url(u):
                            thumbnail_url = u
                            break
                thumb_saved = False
                thumbnail_path = video_path.replace("mp4", "jpg")
                if looks_like_image_url(thumbnail_url):
                    try:
                        resp = requests.get(
                            thumbnail_url,
                            timeout=10,
                            headers={
                                "User-Agent": "Mozilla/5.0",
                                "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
                            },
                        )
                        ctype = (resp.headers.get("Content-Type") or "").lower()
                        if resp.ok and resp.content and ("image/" in ctype or not ctype):
                            with open(thumbnail_path, 'wb') as file:
                                file.write(resp.content)
                            thumb_saved = normalize_thumbnail_to_jpeg(thumbnail_path)
                    except Exception:
                        thumb_saved = False
                title = info_dict.get('title', 'No name')
                await state.update_data(link=link)
                await state.update_data(title=title)
                await state.update_data(domain=domain)
                await state.update_data(video_path=video_path)
                await state.update_data(thumbnail_path=thumbnail_path)
                try:
                    public_ok = is_youtube_public(link)
                except Exception:
                    public_ok = False
                premium_mode = not public_ok
                await state.update_data(premium=premium_mode)
                # All users are free; show formats without prices
                kb = youtube_formats_kb(formats, free=True, force_paid=False, price=None)
                caption_text = "<tg-emoji emoji-id=\"5375309569905938163\">⭐</tg-emoji>"+title
                # Keep premium notice informational only, no payment requirement
                if premium_mode:
                    caption_text += "\n\nNote: This video may require cookies due to age or access restrictions."

                # Telegram photo caption limit is 1024 chars
                caption_for_photo = caption_text[:1024]

                # Send formats keyboard and also show yt-dlp stderr logs (if any)
                sent = False
                if thumb_saved and os.path.exists(thumbnail_path):
                    try:
                        await message.answer_photo(photo=FSInputFile(thumbnail_path), caption=caption_for_photo, reply_markup=kb)
                        sent = True
                    except Exception as e:
                        try:
                            size = os.path.getsize(thumbnail_path) if os.path.exists(thumbnail_path) else -1
                            print(f"answer_photo local thumb failed: {e!r}; path={thumbnail_path}; size={size}")
                        except Exception:
                            pass
                        sent = False
                if (not sent) and looks_like_image_url(thumbnail_url):
                    try:
                        await message.answer_photo(photo=thumbnail_url, caption=caption_for_photo, reply_markup=kb)
                        sent = True
                    except Exception as e:
                        try:
                            print(f"answer_photo URL thumb failed: {e!r}; url={thumbnail_url}")
                        except Exception:
                            pass
                        sent = False
                if not sent:
                    await message.answer(caption_text, reply_markup=kb)
                # yt-dlp logs are printed to server console only (not sent to Telegram)
            else:
                if domain.find("tiktok") > -1 or domain.find("instagram") > -1 or domain.find("pinterest") > -1 or domain.find("vk.com") > -1 or domain.find("x.com") > -1 or domain.find("twitter.com") > -1:
                    db.set_work(message.from_user.id, 1)
                    # Сообщение о начале загрузки будет отправлено через send_download_started_message
                    # Генерируем ID для загрузки
                    download_id = generate_download_id(message.from_user.id)
                    
                    # Отправляем сообщение о начале загрузки
                    message_id = send_download_started_message(message.chat.id, download_id, link)

                    # Сохраняем информацию о загрузке в БД
                    db.add_active_download(
                        download_id=download_id,
                        user_id=message.from_user.id,
                        chat_id=message.chat.id,
                        url=link,
                        format_id=None,
                        file_path=video_path,
                        message_id=message_id
                    )
                    my_thread = threading.Thread(target=simple_downloader_with_cancel, args=(link, video_path, message.chat.id, domain, None, title_orig, None, message.from_user.id, str(message.chat.id), download_id))
                    my_thread.start()
                else:
                    await send_start_message(message)
        else:
            await send_start_message(message)
    except:
        print(traceback.format_exc())
        await send_start_message(message)

async def all(message: Message, state: FSMContext):
    # Channel connected to chat events + anonymous users off
    if message.from_user.id in [777000, 1087968824, 136817688]:
        return
    try:
        chat_type = getattr(message.chat, 'type', 'private')
        text = message.text or ""
        import re as _re
        m = _re.search(r'(https?://\S+)', text)
        link = m.group(1) if m else None
        if not link:
            if chat_type in ("group", "supergroup"):
                return
            await send_start_message(message)
            return
        domain = get_domain(link)
        if not is_supported_domain(domain):
            if chat_type in ("group", "supergroup"):
                return
            else:
                await send_start_message(message)
                return
        if domain and 'youtu' in domain and is_youtube_playlist_like(link):
            # Ignore playlist/mix; in private chat inform user, in groups stay silent
            if chat_type in ("group", "supergroup"):
                return
            await message.answer("YouTube playlist/mix links are ignored. Please send a direct link like https://youtube.com/watch?v=ID without list=.")
            return
        await process_link_message(message, state, link)
    except:
        if getattr(message.chat, 'type', 'private') not in ("group", "supergroup"):
            await send_start_message(message)


# Payments removed: no pre-checkout, successful payment, or refund handlers


async def start_mail(message: Message, state: FSMContext):
    if str(message.from_user.id) not in config.admin_list:
        return
    await message.answer("Send a message to forward to all users\n/cancel to cancel.")
    await state.set_state(CatchMessageState.message)

async def confirm_mail(message: Message, state: FSMContext):
    await state.clear()
    if message.text == "/cancel":
        await message.answer(f"{tge('no', '❌')} Denied!")
        return
    txt = message.html_text
    file_id = None
    m_type = "text"
    if message.photo:
        m_type = "photo"
        file_id = message.photo[-1].file_id
        await message.answer_photo(caption=txt, photo=file_id)
    elif message.video:
        m_type = "video"
        file_id = message.video.file_id
        await message.answer_video(caption=txt, video=file_id)
    elif message.animation:
        m_type = "animation"
        file_id = message.animation.file_id
        await message.answer_animation(caption=txt, animation=file_id)
    if message.text:
        await message.answer(text=txt)
    await state.update_data(txt=txt)
    await state.update_data(file_id=file_id)
    await state.update_data(m_type=m_type)
    await message.answer("Send message to all users?", reply_markup=confirm_mail_kb())

async def mailer(call: CallbackQuery, state: FSMContext):
    _, res = call.data.split(":")
    if res == "0":
        await call.message.delete()
        await call.message.answer("Canceled")
        await state.clear()
        return
    data = await state.get_data()
    txt = data['txt']
    file_id = data['file_id']
    m_type = data['m_type']
    users = db.get_users()
    success = 0
    bad = 0
    if m_type == "photo":
        for user in users:
            try:
                await call.bot.send_photo(
                    chat_id=user[0],
                    caption=txt,
                    photo=file_id
                )
                success += 1
            except:
                bad += 1
    if m_type == "video":
        for user in users:
            try:
                await call.bot.send_video(
                    chat_id=user[0],
                    caption=txt,
                    video=file_id
                )
                success += 1
            except:
                bad += 1
    if m_type == "animation":
        for user in users:
            try:
                await call.bot.send_animation(
                    chat_id=user[0],
                    caption=txt,
                    animation=file_id
                )
                success += 1
            except:
                bad += 1

    if m_type == "text":
        for user in users:
            try:
                await call.bot.send_message(
                    chat_id=user[0],
                    text=txt
                )
                success += 1
            except:
                bad += 1
    await call.message.answer(f"Success: {success}\nBad: {bad}")

async def inline_query_handler(query: InlineQuery, state: FSMContext):
    q = (query.query or '').strip()
    import re as _re
    m = _re.search(r'(https?://\S+)', q)
    if not m:
        result = InlineQueryResultArticle(
            id='help',
            title='Paste a link to download',
            description='Example: https://youtube.com/watch?v=... or other supported link',
            input_message_content=InputTextMessageContent(message_text='Paste a link to download')
        )
        await query.answer([result], cache_time=5, is_personal=True)
        return
    link = m.group(1)
    domain = get_domain(link)
    title = 'No name'
    thumb_url = None
    kb = None
    try:
        info_dict, ytlog = get_video_formats(link, domain)
        title = info_dict.get('title', 'No name')
        thumb_url = info_dict.get('thumbnail')
        if not looks_like_image_url(thumb_url):
            for th in (info_dict.get('thumbnails') or []):
                u = th.get('url') if isinstance(th, dict) else None
                if looks_like_image_url(u):
                    thumb_url = u
                    break
    except Exception:
        ytlog = ''
        pass

        bot_info = await query.bot.get_me()
    bot_username = bot_info.username
    token = 'dl_' + ''.join(random.choices(string.ascii_letters + string.digits, k=10))
    
    # Сохраняем полную информацию о видео в deeplink
    video_info = {
        'link': link,
        'domain': domain,
        'title': title,
        'thumbnail_url': thumb_url
    }
    
    try:
        db.add_deeplink(token, json.dumps(video_info))
    except Exception:
        # Fallback: сохраняем только ссылку
        try:
            db.add_deeplink(token, link)
        except Exception:
            pass
    
    deeplink = f"https://t.me/{bot_username}?start={token}"
    kb_builder = InlineKeyboardBuilder()
    kb_builder.row(ikb("Open bot to download", url=deeplink, style="primary"))
    pm_kb = kb_builder.as_markup()

    caption_text = title
    if thumb_url and looks_like_image_url(thumb_url):
        result = InlineQueryResultPhoto(
            id='parsed_photo',
            photo_url=thumb_url,
            thumbnail_url=thumb_url,
            caption=caption_text,
            reply_markup=pm_kb
        )
    else:
        result = InlineQueryResultArticle(
            id='parsed',
            title=title,
            description='Open bot to download',
            input_message_content=InputTextMessageContent(message_text='Tap the button below to open the bot and choose quality.'),
            reply_markup=pm_kb
        )
    await query.answer([result], cache_time=0, is_personal=True)

async def check_subscription(call: CallbackQuery):
    try:
        chat_type = getattr(call.message.chat, 'type', 'private') if call.message else 'private'
        if chat_type in ("group", "supergroup"):
            try:
                if call.message:
                    await call.message.delete()
            except Exception:
                pass
            try:
                await call.answer()
            except Exception:
                pass
            return

        user_id = call.from_user.id
        ch_id = config.channel_id
        if not ch_id:
            if call.message:
                try:
                    await call.message.delete()
                except Exception:
                    pass
                await send_start_message(call.message)
            else:
                await call.answer()
            return

        member = await call.bot.get_chat_member(chat_id=ch_id, user_id=user_id)
        status = getattr(member, 'status', None)
        if status in ["member", "administrator", "creator"]:
            if call.message:
                try:
                    await call.message.delete()
                except Exception:
                    pass
                await send_start_message(call.message)
            else:
                await call.answer()
        else:
            await call.answer("Subscribe to the channel to use the bot", show_alert=True)
    except Exception as e:
        if call.message and getattr(call.message.chat, 'type', 'private') in ("group", "supergroup"):
            try:
                await call.answer()
            except Exception:
                pass
        else:
            try:
                await call.answer("Failed to check subscription. Please try again later.", show_alert=True)
            except Exception:
                pass
        print(f"check_subscription error: {e}")

async def ban_user(call: CallbackQuery):
    """Ban user from the channel using callback data ban:<user_id>. Only admins allowed."""
    try:
        if str(call.from_user.id) not in config.admin_list:
            await call.answer("No rights", show_alert=True)
            return
        _, user_id_str = call.data.split(":", 1)
        target_id = int(user_id_str)
        await call.bot.ban_chat_member(chat_id=config.channel_id, user_id=target_id)
        try:
            await call.message.edit_text(f"User <code>{target_id}</code> banned", disable_web_page_preview=True)
        except Exception:
            await call.message.answer(f"User <code>{target_id}</code> banned")
        await call.answer("Banned")
    except Exception as e:
        print(f"Ban error: {e}")
        try:
            await call.answer("Ban failed", show_alert=True)
        except Exception:
            pass


async def cancel_download_command(message: Message, state: FSMContext):
    """Handle /cancel command: show active downloads available for cancel."""
    user_id = message.from_user.id
    
    # Получаем активные загрузки пользователя
    active_downloads = db.get_active_downloads(user_id)
    
    if not active_downloads:
        await message.answer("You have no active downloads to cancel.")
        return
    
    # Создаем клавиатуру с активными загрузками
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    from aiogram import types
    
    keyboard_builder = InlineKeyboardBuilder()
    
    for i, (download_id, url, format_id, started_at) in enumerate(active_downloads, 1):
        # Форматируем время
        from datetime import datetime
        time_str = datetime.fromtimestamp(started_at).strftime('%H:%M:%S')
        
        # Создаем короткое описание
        url_short = url[:30] + '...' if len(url) > 30 else url
        button_text = f"{i}. {url_short} ({time_str})"
        
        keyboard_builder.row(
            cancel_download_btn(download_id, text=button_text)
        )
    
    await message.answer(
        "Select a download to cancel:",
        reply_markup=keyboard_builder.as_markup()
    )


async def cancel_download_callback(call: CallbackQuery, state: FSMContext):
    """Handle cancel-download button click."""
    try:
        # Извлекаем download_id из callback_data
        _, download_id = call.data.split(":", 1)
        
        # Получаем информацию о загрузке
        download_info = db.get_download_by_id(download_id)
        if not download_info:
            await call.answer("Download not found or already finished.", show_alert=True)
            return
        
        # Проверяем, что пользователь отменяет свою загрузку
        user_id = call.from_user.id
        if download_info[1] != user_id:  # user_id field
            await call.answer("You cannot cancel someone else's download.", show_alert=True)
            return
        
        # Пытаемся отменить загрузку
        success, message = cancel_download_process(download_id)
        safe_msg = html.escape(str(message))
        
        if success:
            # Обновляем сообщение
            if call.message:
                try:
                    await call.message.edit_text(
                        f"{tge('check', '✅')} {safe_msg}\n\nDownload canceled.",
                        reply_markup=None
                    )
                except Exception:
                    await call.message.answer(f"{tge('check', '✅')} {safe_msg}\n\nDownload canceled.")
            else:
                await call.answer(f"{tge('check', '✅')} {safe_msg}", show_alert=True)
            
            # Сбрасываем work статус пользователя
            db.set_work(user_id, 0)
        else:
            if call.message:
                await call.message.answer(f"{tge('no', '❌')} Failed to cancel download: {safe_msg}")
            else:
                await call.answer(f"{tge('no', '❌')} Failed to cancel download: {safe_msg}", show_alert=True)
        
    except Exception as e:
        print(f"Error in cancel_download_callback: {e}")
        try:
            await call.answer("An error occurred while canceling the download.", show_alert=True)
        except Exception:
            pass


async def delete_formats_msg_callback(call: CallbackQuery):
    try:
        if call.message:
            try:
                await call.message.delete()
            except Exception:
                pass
        try:
            await call.answer()
        except Exception:
            pass
    except Exception:
        try:
            await call.answer()
        except Exception:
            pass


async def main():
    db.reset_work()
    # Очищаем старые записи о загрузках (старше 24 часов)
    db.cleanup_old_downloads(24)
    clear_downloads()
    # Ensure dlp folder has the two latest yt-dlp releases before bot starts
    try:
        dlp_manager.download_latest_releases(2)
    except Exception as e:
        print(f"dlp_manager error: {e}")
    bot_properties = DefaultBotProperties(parse_mode=ParseMode.HTML)
    bot = Bot(token=bot_token, default=bot_properties)

    # Don't process old (pending) updates after bot restarts
    # Works for polling as well: Telegram will drop queued updates.
    try:
        await bot.delete_webhook(drop_pending_updates=True)
    except Exception as e:
        print(f"Failed to drop pending updates: {e}")

    dp = Dispatcher(storage=MemoryStorage())
    dp.message.middleware(ExistsUserMiddleware())
    dp.message.middleware(ThrottlingMiddleware())

    dp.message.register(welcome, Command(commands="start"))
    dp.message.register(start_mail, Command(commands="mail"))
    dp.message.register(cancel_download_command, Command(commands="cancel"))
    dp.message.register(confirm_mail, CatchMessageState.message)
    dp.callback_query.register(mailer, F.data.startswith("mailer"))
    dp.callback_query.register(youtube_download, F.data.startswith("youtube_download"))
    # Payment handlers removed
    dp.callback_query.register(check_subscription, F.data == "check_subscription")
    dp.callback_query.register(ban_user, F.data.startswith("ban:"))
    dp.callback_query.register(cancel_download_callback, F.data.startswith("cancel_download:"))
    dp.callback_query.register(delete_formats_msg_callback, F.data == "delete_formats_msg")
    dp.inline_query.register(inline_query_handler)
    dp.message.register(all)

    print("Bot started")
    # start background scheduler (3-hour DB backup to LOG_CHAT)
    try:
        asyncio.create_task(scheduler.run_backup_scheduler(bot))
    except Exception as e:
        print(f"Failed to start backup scheduler: {e}")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

