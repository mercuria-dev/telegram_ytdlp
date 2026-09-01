import asyncio
import logging
from logging.handlers import RotatingFileHandler
import sys
import faulthandler
import signal
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
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
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


def _setup_logging() -> None:
    """Configure rotating file logs + crash hooks.

    Notes:
      - SIGKILL (e.g., Linux OOM killer) cannot be caught in-process.
        For that case we rely on the external restart script + last lines in logs.
    """
    if getattr(_setup_logging, "_configured", False):
        return

    try:
        os.makedirs("logs", exist_ok=True)
    except Exception:
        # If we can't create logs folder, still configure console logging.
        pass

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    fmt = logging.Formatter(
        fmt="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console
    sh = logging.StreamHandler(stream=sys.stdout)
    sh.setLevel(logging.INFO)
    sh.setFormatter(fmt)
    root.addHandler(sh)

    # Main bot log
    try:
        fh = RotatingFileHandler(
            "logs/bot.log",
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        fh.setLevel(logging.INFO)
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except Exception:
        pass

    # Crash-focused log (errors+tracebacks)
    try:
        ch = RotatingFileHandler(
            "logs/crash.log",
            maxBytes=5 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        ch.setLevel(logging.ERROR)
        ch.setFormatter(fmt)
        root.addHandler(ch)
    except Exception:
        pass

    # Enable faulthandler (captures segfaults, fatal signals, etc.)
    try:
        _fh_file = open("logs/fault.log", "a", buffering=1, encoding="utf-8")
        faulthandler.enable(file=_fh_file, all_threads=True)

        # Register common fatal-ish signals where supported.
        for sig_name in ("SIGABRT", "SIGSEGV", "SIGFPE", "SIGILL", "SIGBUS"):
            sig = getattr(signal, sig_name, None)
            if sig is None:
                continue
            try:
                faulthandler.register(sig, file=_fh_file, all_threads=True, chain=True)
            except Exception:
                pass
    except Exception:
        pass

    def _excepthook(exc_type, exc, tb):
        logging.getLogger("crash").critical("Uncaught exception", exc_info=(exc_type, exc, tb))

    sys.excepthook = _excepthook
    _setup_logging._configured = True


def _install_asyncio_exception_handler() -> None:
    try:
        loop = asyncio.get_running_loop()
    except Exception:
        return

    def _handler(loop, context):
        msg = context.get("message")
        exc = context.get("exception")
        logging.getLogger("asyncio").error(
            "Asyncio exception: %s", msg or context, exc_info=exc
        )

    try:
        loop.set_exception_handler(_handler)
    except Exception:
        pass

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


# ---------------------------------------------------------------- star balance
#
# A user tops the balance up once (10/50/100/200/500/1000 ⭐) and every paid
# download is then silently debited from it. Only when the balance can't cover
# the price do we fall back to the old per-download Telegram Stars invoice.

def balance_enabled() -> bool:
    return bool(getattr(config, 'balance_enabled', True))


def topup_options() -> list[int]:
    return list(getattr(config, 'stars_topup_options', []) or [])


def payment_nonce(k: int = 6) -> str:
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=k))


def make_balance_ref(user_id: int, amount: int) -> str:
    """Unique token for one balance spend.

    It doubles as the refund callback payload, so the price is embedded in it:
    `bal:<user_id>:<amount>:<nonce>`.
    """
    return f"bal:{user_id}:{int(amount)}:{payment_nonce()}"


def charge_balance(user_id: int, amount: int) -> str | None:
    """Debit `amount` stars. Returns the spend ref, or None if the balance can't cover it."""
    if not balance_enabled() or int(amount) <= 0:
        return None
    ref = make_balance_ref(user_id, amount)
    try:
        if db.spend_balance(user_id, int(amount), ref):
            return ref
    except Exception as e:
        print(f"charge_balance error: {e}")
    return None


def get_balance(user_id: int) -> int:
    try:
        return int(db.get_balance(user_id) or 0)
    except Exception:
        return 0


def balance_paid_note(user_id: int, amount: int) -> str:
    return f"Paid {int(amount)} ⭐ from balance.\nBalance left: {get_balance(user_id)} ⭐"


def balance_text(user_id: int) -> str:
    balance = get_balance(user_id)
    lines = [
        f"{tge('gem', '💎')} <b>Your balance: {balance} ⭐</b>",
        "",
        "The balance is optional. When it covers the price, a paid download is "
        "charged from it automatically — no invoice to confirm each time. "
        "Otherwise you just pay for that one download with Stars as usual.",
        "If a download fails or you cancel it, the stars are returned.",
        "",
        "Choose how many stars to add:",
    ]
    return "\n".join(lines)


def is_admin(user_id) -> bool:
    """Admin check that tolerates spaces in ADMIN_LIST."""
    return str(user_id) in {a.strip() for a in config.admin_list if a.strip()}


def is_free_whitelisted_user(user_id: int) -> bool:
    return str(user_id) in {s.strip() for s in config.free_whitelist if s.strip()}


def is_free_user(user_id: int) -> bool:
    """Whitelist and admins never pay."""
    return is_free_whitelisted_user(user_id) or is_admin(user_id)

def other_services_price_for_user(user_id: int) -> int:
    if is_free_user(user_id):
        return 0
    if not getattr(config, 'paid_other_services', True):
        return 0
    return int(getattr(config, 'other_services_stars_price', config.stars_price) or 0)

def service_display_name(domain: str | None) -> str:
    d = (domain or '').lower()
    if 'soundcloud.com' in d:
        return 'SoundCloud'
    if 'tiktok' in d:
        return 'TikTok'
    if 'instagram' in d:
        return 'Instagram'
    if 'pinterest' in d:
        return 'Pinterest'
    if d == 'vk.com' or 'vkvideo.ru' in d:
        return 'VK'
    if d in {'x.com', 'twitter.com'} or 'twitter.com' in d:
        return 'X / Twitter'
    return 'Video'

async def charge_service_download(
    message: Message,
    state: FSMContext,
    *,
    link: str,
    domain: str,
    video_path: str,
    thumbnail_path: str | None,
    title: str,
    fmt: str | None,
    item_title: str | None = None,
) -> tuple[bool, str | None]:
    """Take payment for a non-YouTube download.

    Returns (handled, balance_ref):
      - (True, None)  -> an invoice was sent, the caller must stop and wait for payment
      - (False, ref)  -> paid from the balance, the caller downloads and passes `ref`
                         along as the payment payload so a failure can be refunded
      - (False, None) -> download is free, nothing to charge
    """
    item_price = other_services_price_for_user(message.from_user.id)
    if item_price <= 0:
        return False, None

    service_name = service_display_name(domain)
    item_title = item_title or f'{service_name} download'

    balance_ref = charge_balance(message.from_user.id, item_price)
    if balance_ref:
        try:
            await message.answer(balance_paid_note(message.from_user.id, item_price))
        except Exception:
            pass
        return False, balance_ref

    # Balance couldn't cover it — charge this one download directly via Telegram.
    await state.update_data(purchase={
        'type': 'audio' if fmt == 'audio' else 'video',
        'format': fmt,
        'size': 0,
        'note': service_name,
        'link': link,
        'domain': domain,
        'video_path': video_path,
        'thumbnail_path': thumbnail_path,
        'title': title,
        'price': item_price,
    })

    payload_service = (domain or 'download').replace(':', '_')[:20]
    payload = f'svc:{payload_service}:{message.from_user.id}:{payment_nonce()}'
    await state.update_data(purchase_payload=payload)

    prices = [LabeledPrice(label=item_title, amount=item_price)]
    try:
        await message.bot.send_invoice(
            chat_id=message.chat.id,
            title=item_title,
            description=f'Pay {item_price} ⭐ to download',
            payload=payload,
            provider_token=None,
            currency='XTR',
            prices=prices,
        )
    except Exception as e:
        await message.answer("Couldn't create invoice. Please try again later.")
        print(f"Service invoice error: {e}")
        return True, None

    return True, None

start_msg = (
    "<tg-emoji emoji-id=\"5373230968943420212\">⭐</tg-emoji> Good Day!\n"
    "This is an <a href=\"https://github.com/mercuria-dev/telegram_ytdlp\">open-source video downloader</a> on telegram\n"
    "This bot can download:\n\n"
    "Photos and videos from Instagram and TikTok.\n"
    "Videos (with quality selection) and audio (in the best quality) from YouTube.\n"
    "Music from SoundCloud.\n"
    "\n"
    "Paid downloads are billed in Telegram Stars. Optionally top up a balance with "
    "/balance and they get charged from it automatically instead. "
    "If a download fails, the stars come back either way.\n"
    "\n"
    "If you want to support the project, you can donate via Crypto Bot.\n"
    "Press the button below.\n"
)


def load_deeplink_context(token: str) -> tuple[str | None, dict | None]:
    raw = db.get_deeplink(token)
    if not raw:
        return None, None

    try:
        context = json.loads(raw)
        if isinstance(context, dict):
            link = context.get("link")
            if isinstance(link, str) and link:
                return link, context
    except Exception:
        pass

    if isinstance(raw, str) and raw:
        return raw, {"link": raw}
    return None, None

async def send_start_message(message: Message):
    kb = start_kb(is_admin=is_admin(message.from_user.id))
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
        link, _ = load_deeplink_context(token)
        if not link:
            await message.answer("Link is invalid or expired.")
            return
        try:
            db.delete_deeplink(token)
        except Exception:
            pass
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
                _, context = load_deeplink_context(token)
                if context:
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

    is_whitelisted = is_free_user(call.from_user.id)
    if is_whitelisted:
        requires_payment = False
        item_price = 0
    else:
        if premium_mode:
            item_price = config.stars_premium_price
            requires_payment = item_price > 0
        else:
            item_price = config.stars_price
            # Everything above the free ceiling is paid, whatever the exact
            # height is - 640p/854p/1024p used to slip through for free.
            requires_payment = item_price > 0 and ((format == "audio") or is_paid_quality(note))

    if call.message:
        try:
            await call.message.delete()
        except Exception:
            pass

    # The balance pays when it can cover the price; otherwise Telegram bills
    # this single download. Never nag an empty balance about topping up.
    balance_ref = None
    if requires_payment:
        balance_ref = charge_balance(call.from_user.id, item_price)
        if balance_ref:
            requires_payment = False
            note_text = balance_paid_note(call.from_user.id, item_price)
            try:
                if call.message:
                    await call.message.answer(note_text)
                else:
                    await call.answer(note_text, show_alert=True)
            except Exception:
                pass

    if requires_payment:
        purchase_title = data['title'] if data else video_title
        await state.update_data(purchase={
            'type': 'audio' if format == 'audio' else 'video',
            'format': format,
            'size': int(size),
            'note': note,
            'link': link,
            'domain': domain,
            'video_path': video_path,
            'thumbnail_path': thumbnail_path,
            'title': purchase_title,
            'price': item_price,
        })
        suffix = ":prem" if premium_mode else ""
        item_title = ("YouTube Audio" if format == "audio" else f"YouTube {note or 'video'}") + (" • Premium" if premium_mode else "")
        prices = [LabeledPrice(label=item_title, amount=item_price)]
        payload = f"yt:{'audio' if format == 'audio' else 'video'}:{format}:{call.from_user.id}{suffix}:{payment_nonce()}"
        await state.update_data(purchase_payload=payload)
        try:
            target_chat_id = call.message.chat.id if call.message else call.from_user.id
            await call.bot.send_invoice(
                chat_id=target_chat_id,
                title=item_title,
                description=f"Pay {item_price} ⭐ to download",
                payload=payload,
                provider_token=None,
                currency="XTR",
                prices=prices,
            )
        except Exception as e:
            if call.message:
                await call.message.answer("Couldn't create invoice. Please try again later.")
            else:
                await call.answer("Couldn't create invoice. Open the bot in PM and try again.", show_alert=True)
            print(f"Invoice error: {e}")
        return

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
        message_id=message_id,
        payment_ref=balance_ref,
    )

    if format != "audio":
        title_for_send = title  # Используем title из context или data
        # Используем новую функцию с поддержкой отмены
        my_thread = threading.Thread(
            target=simple_downloader_with_cancel, 
            args=(link, video_path, target_chat_id, domain, format, title_for_send, 
                  thumbnail_path, call.from_user.id, sess_id, download_id, balance_ref)
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
                  call.from_user.id, sess_id, download_id, balance_ref)
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
                handled, balance_ref = await charge_service_download(
                    message,
                    state,
                    link=link,
                    domain=domain,
                    video_path=video_path,
                    thumbnail_path=thumbnail_path,
                    title=title_orig,
                    fmt='audio',
                    item_title=f'{service_display_name(domain)} Audio',
                )
                if handled:
                    return

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
                    message_id=message_id,
                    payment_ref=balance_ref,
                )
                my_thread = threading.Thread(target=download_audio_with_cancel, args=(link, audio_path, message.chat.id, thumbnail_path, bot_username, message.from_user.id, str(message.chat.id), download_id, balance_ref))
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
                is_whitelisted = is_free_user(message.from_user.id)
                premium_price_enabled = config.stars_premium_price > 0
                standard_price_enabled = config.stars_price > 0
                free_user = is_whitelisted or (premium_mode and not premium_price_enabled) or ((not premium_mode) and not standard_price_enabled)
                force_paid = premium_mode and (not is_whitelisted) and premium_price_enabled
                price_for_buttons = config.stars_premium_price if premium_mode else config.stars_price
                kb = youtube_formats_kb(formats, free=free_user, force_paid=force_paid, price=price_for_buttons)
                caption_text = "<tg-emoji emoji-id=\"5375309569905938163\">⭐</tg-emoji>"+title
                if premium_mode:
                    if premium_price_enabled and not is_whitelisted:
                        caption_text += f"\n\nNote: This video is age-restricted (18+) or has limited access on YouTube and is only accessible with cookies. All download options require {config.stars_premium_price} ⭐."
                    else:
                        caption_text += "\n\nNote: This video is age-restricted (18+) or has limited access on YouTube and is only accessible with cookies."

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
                    handled, balance_ref = await charge_service_download(
                        message,
                        state,
                        link=link,
                        domain=domain,
                        video_path=video_path,
                        thumbnail_path=None,
                        title=title_orig,
                        fmt=None,
                        item_title=f'{service_display_name(domain)} download',
                    )
                    if handled:
                        return

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
                        message_id=message_id,
                        payment_ref=balance_ref,
                    )
                    my_thread = threading.Thread(target=simple_downloader_with_cancel, args=(link, video_path, message.chat.id, domain, None, title_orig, None, message.from_user.id, str(message.chat.id), download_id, balance_ref))
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


async def balance_command(message: Message):
    if not balance_enabled():
        return
    await message.answer(balance_text(message.from_user.id), reply_markup=balance_kb())


async def balance_callback(call: CallbackQuery):
    action = call.data.split(":", 1)[1] if ":" in call.data else "show"

    if action == "close":
        try:
            await call.message.delete()
        except Exception:
            pass
        await call.answer()
        return

    if not balance_enabled():
        await call.answer("Balance is disabled", show_alert=True)
        return

    text = balance_text(call.from_user.id)
    in_group = bool(call.message) and getattr(call.message.chat, 'type', 'private') in ("group", "supergroup")
    try:
        # /start sends a photo, whose caption can't be swapped for a plain text message.
        # In a group the message may have been opened by someone else, so post a
        # fresh one instead of overwriting it with this user's balance.
        if call.message and call.message.text and not in_group:
            await call.message.edit_text(text, reply_markup=balance_kb())
        elif call.message:
            await call.message.answer(text, reply_markup=balance_kb())
        else:
            await call.bot.send_message(call.from_user.id, text, reply_markup=balance_kb())
    except Exception as e:
        print(f"balance_callback error: {e}")
        try:
            await call.message.answer(text, reply_markup=balance_kb())
        except Exception:
            pass
    await call.answer()


async def topup_callback(call: CallbackQuery):
    if not balance_enabled():
        await call.answer("Balance is disabled", show_alert=True)
        return

    try:
        amount = int(call.data.split(":", 1)[1])
    except Exception:
        await call.answer("Invalid amount", show_alert=True)
        return

    # Only the advertised amounts, so a crafted callback can't invent its own price.
    if amount not in topup_options():
        await call.answer("Invalid amount", show_alert=True)
        return

    payload = f"topup:{amount}:{call.from_user.id}:{payment_nonce()}"
    title = f"Top up {amount} ⭐"
    invoice = dict(
        title=title,
        # Whoever pays is the one credited, so "your" is accurate in a group too.
        description=f"Add {amount} ⭐ to your balance in this bot. "
                    f"Paid downloads are then charged automatically.",
        payload=payload,
        provider_token=None,
        currency="XTR",
        prices=[LabeledPrice(label=title, amount=amount)],
    )

    # Deliver the invoice where the button was pressed, so a top-up started in a
    # group is finished in that group. Inline mode has no chat of its own -> PM.
    target_chat_id = call.message.chat.id if call.message else call.from_user.id
    try:
        await call.bot.send_invoice(chat_id=target_chat_id, **invoice)
        await call.answer()
        return
    except Exception as e:
        print(f"Top-up invoice error in chat {target_chat_id}: {e}")

    if target_chat_id == call.from_user.id:
        await call.answer("Couldn't create invoice. Please try again later.", show_alert=True)
        return

    # The chat wouldn't take the invoice (permissions, or a chat type Telegram
    # refuses to bill in) — fall back to PM rather than losing the top-up.
    try:
        await call.bot.send_invoice(chat_id=call.from_user.id, **invoice)
        await call.answer("Invoice sent to you in a private message.", show_alert=True)
    except Exception as e:
        print(f"Top-up invoice PM fallback error: {e}")
        await call.answer(
            "Couldn't create invoice here. Open the bot in PM and use /balance.",
            show_alert=True,
        )


async def admin_add_balance(message: Message):
    """/addbalance <user_id> <amount> — grant (or take back, with a negative amount) stars."""
    if not is_admin(message.from_user.id):
        return

    parts = (message.text or "").split()
    if len(parts) < 3:
        await message.answer("Usage: <code>/addbalance user_id amount</code>")
        return

    try:
        target_id = int(parts[1])
        amount = int(parts[2])
    except ValueError:
        await message.answer("user_id and amount must be numbers")
        return

    new_balance = db.add_balance(target_id, amount, kind='admin', ref=f"admin:{message.from_user.id}")
    await message.answer(f"Balance of <code>{target_id}</code> is now {new_balance} ⭐")
    try:
        await message.bot.send_message(
            target_id,
            f"Your balance was {'topped up by' if amount >= 0 else 'reduced by'} "
            f"{abs(amount)} ⭐.\nBalance: {new_balance} ⭐",
        )
    except Exception:
        pass


async def admin_show_balance(message: Message):
    """/getbalance <user_id> — read someone's balance."""
    if not is_admin(message.from_user.id):
        return

    parts = (message.text or "").split()
    if len(parts) < 2:
        await message.answer("Usage: <code>/getbalance user_id</code>")
        return
    try:
        target_id = int(parts[1])
    except ValueError:
        await message.answer("user_id must be a number")
        return

    await message.answer(f"Balance of <code>{target_id}</code>: {get_balance(target_id)} ⭐")


async def pre_checkout_handler(pre_checkout_q: PreCheckoutQuery):
    try:
        await pre_checkout_q.bot.answer_pre_checkout_query(pre_checkout_q.id, ok=True)
    except Exception as e:
        print(f"PreCheckout answer error: {e}")


async def credit_topup(message: Message, payload: str, charge_id: str | None, paid_amount: int) -> None:
    """Add a paid top-up to the user's balance."""
    try:
        amount = int(payload.split(":")[1])
    except Exception:
        amount = 0
    # Trust what Telegram says was actually paid.
    if paid_amount > 0:
        amount = paid_amount

    if amount <= 0:
        await message.answer("Top-up failed: invalid amount. Contact the admin.")
        return

    try:
        if charge_id:
            db.add_payment(user_id=message.from_user.id, payload=payload, charge_id=charge_id, amount=amount)
    except Exception as e:
        print(f"Failed to save top-up payment: {e}")

    new_balance = db.add_balance(message.from_user.id, amount, kind='topup', ref=payload)
    kb = None
    if charge_id:
        kb = AioInlineKeyboardMarkup(
            inline_keyboard=[[AioInlineKeyboardButton(text=f"🔄 Refund {amount}⭐", callback_data=f"refund:{payload}")]]
        )
    await message.answer(
        f"Balance topped up by {amount} ⭐ ✅\n"
        f"Your balance: {new_balance} ⭐\n\n"
        "Just send a link — paid downloads are now charged from the balance.",
        reply_markup=kb,
    )


async def on_successful_payment(message: Message, state: FSMContext):
    sp = message.successful_payment
    if not sp:
        return

    payload = sp.invoice_payload or ""
    charge_id = getattr(sp, 'telegram_payment_charge_id', None)
    paid_amount = int(getattr(sp, 'total_amount', 0) or 0)
    purchase = None

    # A balance top-up buys no download, so it's settled before the order lookup.
    if payload.startswith("topup:"):
        await credit_topup(message, payload, charge_id, paid_amount)
        return

    try:
        data = await state.get_data()
        purchase = data.get('purchase')
        if not purchase:
            # The order is gone (bot restarted, or an invoice from before the
            # balance-only switch). Never swallow the stars: bank them instead.
            credited = db.add_balance(message.from_user.id, paid_amount, kind='topup', ref=payload)
            try:
                db.add_payment(user_id=message.from_user.id, payload=payload,
                               charge_id=charge_id or '', amount=paid_amount)
            except Exception as e:
                print(f"Failed to save stray payment: {e}")
            await message.answer(
                f"Couldn't find your order, so {paid_amount} ⭐ were added to your balance.\n"
                f"Your balance: {credited} ⭐\n\nPlease send the link again.",
            )
            return

        try:
            if charge_id:
                db.add_payment(
                    user_id=message.from_user.id,
                    payload=payload,
                    charge_id=charge_id,
                    amount=paid_amount or int(purchase.get('price') or 0),
                )
        except Exception as e:
            print(f"Failed to save payment: {e}")

        link = purchase['link']
        domain = purchase['domain']
        video_path = purchase['video_path']
        thumbnail_path = purchase['thumbnail_path']
        title = sanitize_filename(purchase['title'])
        fmt = purchase['format']

        db.set_work(message.from_user.id, 1)
        await message.answer("Payment received ✅\nStarting download...")

        download_id = generate_download_id(message.from_user.id)
        message_id = send_download_started_message(message.chat.id, download_id, link)
        file_path = video_path if fmt != 'audio' else f"downloads/{title}.mp3"
        db.add_active_download(
            download_id=download_id,
            user_id=message.from_user.id,
            chat_id=message.chat.id,
            url=link,
            format_id=fmt if fmt != 'audio' else 'audio',
            file_path=file_path,
            message_id=message_id,
            payment_ref=payload,
        )

        if fmt != 'audio':
            thread = threading.Thread(
                target=simple_downloader_with_cancel,
                args=(link, video_path, message.chat.id, domain, fmt, purchase['title'], thumbnail_path, message.from_user.id, str(message.chat.id), download_id, payload),
            )
        else:
            audio_path = f"downloads/{title}.mp3"
            bot_info = await message.bot.get_me()
            bot_username = bot_info.username
            thread = threading.Thread(
                target=download_audio_with_cancel,
                args=(link, audio_path, message.chat.id, thumbnail_path, bot_username, message.from_user.id, str(message.chat.id), download_id, payload),
            )

        thread.start()
        await state.update_data(purchase=None)
        await state.update_data(purchase_payload=None)
    except Exception as e:
        print(f"Successful payment handling error: {e}")
        try:
            kb = None
            if payload:
                pay_price = int(
                    (purchase or {}).get('price')
                    or (config.stars_premium_price if (":prem" in str(payload)) else config.stars_price)
                )
                kb = AioInlineKeyboardMarkup(
                    inline_keyboard=[[AioInlineKeyboardButton(text=f"🔄 Refund {pay_price}⭐", callback_data=f"refund:{payload}")]]
                )
            await message.answer("Payment processing error. You can request a refund.", reply_markup=kb)
        except Exception:
            await message.answer("Payment processing error. Please try again.")


async def refund_star_payment(bot_token: str, user_id: int, charge_id: str) -> bool:
    url = f"{config.telegram_api_base}/bot{bot_token}/refundStarPayment"
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json={"user_id": user_id, "telegram_payment_charge_id": charge_id}) as resp:
            if resp.status != 200:
                print(f"refundStarPayment HTTP {resp.status}")
                return False
            data = await resp.json()
            ok = data.get('ok', False)
            if not ok:
                print(f"refundStarPayment failed: {data}")
            return ok


async def refund_payment_record(record) -> tuple[bool, str]:
    if not record:
        return False, "Payment not found"

    payment_id, user_id, payload, charge_id, status, amount = record
    if status == 'refunded':
        return False, "Already refunded"
    if not charge_id:
        return False, "Payment has no Telegram charge id"

    amount = int(amount or 0)
    is_topup = str(payload).startswith("topup:")
    if is_topup:
        # Take the stars back off the balance before returning them, so a user
        # can't refund a top-up they have already spent on downloads.
        if amount <= 0:
            return False, "Top-up has no recorded amount"
        if not db.spend_balance(user_id, amount, f"refund-topup:{payload}"):
            return False, (
                f"Balance is only {db.get_balance(user_id)} ⭐ — "
                f"the {amount} ⭐ top-up has already been spent and can't be refunded."
            )

    ok = await refund_star_payment(config.bot_token, user_id, charge_id)
    if not ok:
        if is_topup:
            # Telegram refused: put the stars back so the balance stays honest.
            db.add_balance(user_id, amount, kind='topup', ref=f"refund-failed:{payload}")
        return False, "Couldn't refund ⭐. Please try again later."

    db.mark_payment_refunded(payload)
    return True, f"Refund completed ✅\nPayment ID: {payment_id}\nUser ID: {user_id}"


async def refund_handler(call: CallbackQuery):
    try:
        _, payload = call.data.split(":", 1)
    except Exception:
        await call.answer("Invalid request", show_alert=True)
        return

    # A download paid from the balance has no Telegram charge — give the stars back
    # to the balance instead.
    if payload.startswith("bal:"):
        parts = payload.split(":")
        if len(parts) < 4 or parts[1] != str(call.from_user.id):
            await call.answer("This is not your payment", show_alert=True)
            return
        ok, reason, new_balance = db.refund_balance_spend(payload)
        if not ok:
            await call.answer(reason, show_alert=True)
            return
        text = f"Refunded to balance ✅\nYour balance: {new_balance} ⭐"
        try:
            await call.message.edit_text(text)
        except Exception:
            await call.answer(text, show_alert=True)
        return

    rec = db.get_payment_by_payload(payload)
    if not rec:
        await call.answer("Payment not found", show_alert=True)
        return

    _id, user_id, _payload, charge_id, status, _amount = rec
    if user_id != call.from_user.id:
        await call.answer("This is not your payment", show_alert=True)
        return

    ok, text = await refund_payment_record(rec)
    if ok:
        try:
            await call.message.edit_text("Refund completed ✅")
        except Exception:
            await call.answer("Refund completed ✅", show_alert=True)
    else:
        await call.answer(text, show_alert=True)


async def admin_do_refund(message: Message):
    if not is_admin(message.from_user.id):
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await message.answer("Usage: <code>/dorefund payment_id_or_charge_id</code>")
        return

    transaction_id = parts[1].strip()
    rec = None
    if transaction_id.isdigit():
        rec = db.get_payment_by_id(int(transaction_id))
    if not rec:
        rec = db.get_payment_by_charge_id(transaction_id)

    ok, text = await refund_payment_record(rec)
    await message.answer(text)


# ------------------------------------------------------------------ admin panel

def admin_panel_text() -> str:
    return (
        f"{tge('ban', '🔨')} <b>Admin panel</b>\n\n"
        "Broadcast — send a message to every user.\n"
        "Set balance — overwrite someone's star balance.\n"
        "Ban user — ban them in the channel.\n"
        "Refund — refund a Stars payment by id.\n"
        "Stats — bot counters."
    )


def admin_stats_text() -> str:
    st = db.admin_stats()
    return (
        f"{tge('check', '📊')} <b>Stats</b>\n\n"
        f"Users: <b>{st['users']}</b>\n"
        f"Downloading now: <b>{st['working']}</b> (active jobs: {st['active_downloads']})\n\n"
        f"Balance holders: <b>{st['holders']}</b>\n"
        f"Stars on balances: <b>{st['balance_total']} ⭐</b>\n\n"
        f"Payments: <b>{st['payments']}</b> (refunded: {st['refunded']})\n"
        f"Stars kept: <b>{st['stars_paid']} ⭐</b>"
    )


async def show_admin_panel(call: CallbackQuery) -> None:
    """Render the panel over the message the button came from."""
    try:
        if call.message and call.message.text:
            await call.message.edit_text(admin_panel_text(), reply_markup=admin_kb())
        elif call.message:
            await call.message.answer(admin_panel_text(), reply_markup=admin_kb())
        else:
            await call.bot.send_message(call.from_user.id, admin_panel_text(), reply_markup=admin_kb())
    except Exception as e:
        print(f"show_admin_panel error: {e}")
        try:
            await call.message.answer(admin_panel_text(), reply_markup=admin_kb())
        except Exception:
            pass


async def admin_panel_command(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.clear()
    await message.answer(admin_panel_text(), reply_markup=admin_kb())


async def admin_callback(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("No rights", show_alert=True)
        return

    action = call.data.split(":", 1)[1] if ":" in call.data else "home"

    if action == "close":
        await state.clear()
        try:
            await call.message.delete()
        except Exception:
            pass
        await call.answer()
        return

    # Leaving any open prompt returns to a clean panel.
    await state.clear()

    if action == "home":
        await show_admin_panel(call)
    elif action == "stats":
        try:
            await call.message.edit_text(admin_stats_text(), reply_markup=admin_back_kb())
        except Exception:
            await call.message.answer(admin_stats_text(), reply_markup=admin_back_kb())
    elif action == "mail":
        await state.set_state(CatchMessageState.message)
        await call.message.edit_text(
            "Send the message to broadcast — text, photo, video or animation.\n"
            "Formatting is preserved.",
            reply_markup=admin_back_kb(cancel=True),
        )
    elif action == "balance":
        await state.set_state(AdminState.set_balance)
        await call.message.edit_text(
            f"{tge('gem', '💎')} <b>Set balance</b>\n\n"
            "Send: <code>user_id amount</code>\n"
            "Example: <code>123456789 500</code>\n\n"
            "The balance is set to exactly that value (not added).\n"
            "Send <code>user_id</code> alone to just look it up.",
            reply_markup=admin_back_kb(cancel=True),
        )
    elif action == "ban":
        await state.set_state(AdminState.ban)
        await call.message.edit_text(
            f"{tge('ban', '🔨')} <b>Ban user</b>\n\n"
            "Send the <code>user_id</code> to ban in the channel.",
            reply_markup=admin_back_kb(cancel=True),
        )
    elif action == "refund":
        await state.set_state(AdminState.refund)
        await call.message.edit_text(
            "↩️ <b>Refund</b>\n\n"
            "Send a <code>payment_id</code> or a <code>charge_id</code>.",
            reply_markup=admin_back_kb(cancel=True),
        )
    else:
        await show_admin_panel(call)

    await call.answer()


async def _admin_prompt_guard(message: Message, state: FSMContext) -> bool:
    """Shared prologue for the panel's text prompts. True = keep handling."""
    if not is_admin(message.from_user.id):
        await state.clear()
        return False
    if (message.text or "").strip() == "/cancel":
        await state.clear()
        await message.answer("Cancelled.", reply_markup=admin_kb())
        return False
    return True


async def admin_set_balance_input(message: Message, state: FSMContext):
    if not await _admin_prompt_guard(message, state):
        return

    parts = (message.text or "").split()
    try:
        target_id = int(parts[0])
    except (IndexError, ValueError):
        await message.answer(
            "Send <code>user_id amount</code>, or <code>user_id</code> to look it up.",
            reply_markup=admin_back_kb(cancel=True),
        )
        return

    if len(parts) == 1:
        await state.clear()
        await message.answer(
            f"User <code>{target_id}</code>\nBalance: <b>{get_balance(target_id)} ⭐</b>",
            reply_markup=admin_kb(),
        )
        return

    try:
        amount = int(parts[1])
    except ValueError:
        await message.answer("The amount must be a number.", reply_markup=admin_back_kb(cancel=True))
        return
    if amount < 0:
        await message.answer("The amount can't be negative.", reply_markup=admin_back_kb(cancel=True))
        return

    before = get_balance(target_id)
    new_balance = db.set_balance(target_id, amount, ref=f"admin:{message.from_user.id}")
    await state.clear()
    if new_balance < 0:
        await message.answer("Couldn't update the balance. Check the logs.", reply_markup=admin_kb())
        return

    await message.answer(
        f"{tge('check', '✅')} Balance of <code>{target_id}</code>: "
        f"{before} ⭐ → <b>{new_balance} ⭐</b>",
        reply_markup=admin_kb(),
    )
    try:
        await message.bot.send_message(
            target_id, f"Your balance was updated by an admin.\nBalance: {new_balance} ⭐"
        )
    except Exception as e:
        print(f"Balance notice to {target_id} failed: {e}")


async def admin_ban_input(message: Message, state: FSMContext):
    if not await _admin_prompt_guard(message, state):
        return

    try:
        target_id = int((message.text or "").split()[0])
    except (IndexError, ValueError):
        await message.answer("Send a numeric <code>user_id</code>.", reply_markup=admin_back_kb(cancel=True))
        return

    if not config.channel_id:
        await state.clear()
        await message.answer("CHANNEL_ID isn't configured, nothing to ban from.", reply_markup=admin_kb())
        return

    await state.clear()
    try:
        await message.bot.ban_chat_member(chat_id=config.channel_id, user_id=target_id)
        await message.answer(f"{tge('ban', '🔨')} User <code>{target_id}</code> banned.", reply_markup=admin_kb())
    except Exception as e:
        print(f"Admin ban error: {e}")
        await message.answer(f"Ban failed: {html.escape(str(e))}", reply_markup=admin_kb())


async def admin_refund_input(message: Message, state: FSMContext):
    if not await _admin_prompt_guard(message, state):
        return

    transaction_id = (message.text or "").strip().split()[0] if (message.text or "").strip() else ""
    if not transaction_id:
        await message.answer("Send a payment id or charge id.", reply_markup=admin_back_kb(cancel=True))
        return

    rec = None
    if transaction_id.isdigit():
        rec = db.get_payment_by_id(int(transaction_id))
    if not rec:
        rec = db.get_payment_by_charge_id(transaction_id)

    await state.clear()
    ok, text = await refund_payment_record(rec)
    await message.answer(text, reply_markup=admin_kb())


async def start_mail(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
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
    if not is_admin(call.from_user.id):
        await call.answer("No rights", show_alert=True)
        return
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
        if not is_admin(call.from_user.id):
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


async def refund_interrupted_downloads(bot: Bot) -> None:
    """Refund downloads that were still running when the bot died.

    Their worker threads went with the process, so no error path ever ran and
    the stars are still debited. Must happen before cleanup_old_downloads()
    drops the rows.
    """
    try:
        rows = db.get_interrupted_paid_downloads() or []
    except Exception as e:
        print(f"refund_interrupted_downloads lookup error: {e}")
        return

    for download_id, user_id, chat_id, payment_ref in rows:
        try:
            note, settled = auto_refund_payment(payment_ref)
        except Exception as e:
            print(f"Interrupted refund error for {download_id}: {e}")
            continue
        if not note:
            if not settled:
                print(f"Interrupted refund still owed for {download_id} ({payment_ref})")
            continue
        print(f"Refunded interrupted download {download_id} for user {user_id}")
        try:
            await bot.send_message(
                chat_id or user_id,
                f"Your download was interrupted when the bot restarted.{note}",
            )
        except Exception as e:
            print(f"Interrupted refund notice failed for {user_id}: {e}")


async def main():
    _setup_logging()
    _install_asyncio_exception_handler()
    db.reset_work()
    clear_downloads()
    # Ensure dlp folder has the two latest yt-dlp releases before bot starts
    try:
        dlp_manager.download_latest_releases(2, channel=config.yt_dlp_channel)
    except Exception as e:
        print(f"dlp_manager error: {e}")
    bot_properties = DefaultBotProperties(parse_mode=ParseMode.HTML)
    session = None
    if config.bot_api_url:
        session = AiohttpSession(api=TelegramAPIServer.from_base(config.bot_api_url))
        print(f"Using local Bot API server: {config.bot_api_url}")
    bot = Bot(token=bot_token, default=bot_properties, session=session)

    # Don't process old (pending) updates after bot restarts
    # Works for polling as well: Telegram will drop queued updates.
    try:
        await bot.delete_webhook(drop_pending_updates=True)
    except Exception as e:
        print(f"Failed to drop pending updates: {e}")

    # Settle downloads killed with the previous process, then drop stale rows.
    await refund_interrupted_downloads(bot)
    db.cleanup_old_downloads(24)

    dp = Dispatcher(storage=MemoryStorage())
    dp.message.middleware(ExistsUserMiddleware())
    dp.message.middleware(ThrottlingMiddleware())

    dp.message.register(welcome, Command(commands="start"))
    dp.message.register(admin_panel_command, Command(commands=["admin", "panel"]))
    dp.message.register(start_mail, Command(commands="mail"))
    dp.message.register(admin_do_refund, Command(commands="dorefund"))
    dp.message.register(balance_command, Command(commands=["balance", "topup"]))
    dp.message.register(admin_add_balance, Command(commands="addbalance"))
    dp.message.register(admin_show_balance, Command(commands="getbalance"))
    # Panel prompts sit above /cancel on purpose: inside a prompt, /cancel must
    # abort the prompt rather than be read as the download-cancel command.
    dp.message.register(admin_set_balance_input, AdminState.set_balance)
    dp.message.register(admin_ban_input, AdminState.ban)
    dp.message.register(admin_refund_input, AdminState.refund)
    dp.message.register(confirm_mail, CatchMessageState.message)
    dp.message.register(cancel_download_command, Command(commands="cancel"))
    dp.callback_query.register(admin_callback, F.data.startswith("admin:"))
    dp.callback_query.register(mailer, F.data.startswith("mailer"))
    dp.callback_query.register(youtube_download, F.data.startswith("youtube_download"))
    dp.callback_query.register(refund_handler, F.data.startswith("refund:"))
    dp.callback_query.register(balance_callback, F.data.startswith("balance:"))
    dp.callback_query.register(topup_callback, F.data.startswith("topup:"))
    dp.pre_checkout_query.register(pre_checkout_handler)
    dp.message.register(on_successful_payment, F.successful_payment)
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
    _setup_logging()
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.getLogger("bot").info("Interrupted by user")
    except Exception:
        logging.getLogger("crash").exception("Bot crashed")
        raise

