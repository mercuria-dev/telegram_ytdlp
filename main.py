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
import threading
import traceback
import requests
import random
import config
import string
import json
import os
from modules import dlp_manager

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}

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
with open("start.txt", "rt", encoding="utf-8") as start_file:
    start_msg = start_file.read()
    start_file.close()

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
    await message.answer(start_msg, reply_markup=remove_kb(), disable_web_page_preview=True)

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
        title = sanitize_filename(context.get('title') or 'Video')
        random_name = random.randint(10000, 99999)
        video_path = f"downloads/{random_name}.mp4"
        thumbnail_path = video_path.replace("mp4", "jpg")
        thumb_url = context.get('thumbnail_url')
        try:
            if looks_like_image_url(thumb_url):
                response = requests.get(thumb_url, timeout=10)
                if response.ok and response.content:
                    with open(thumbnail_path, 'wb') as file:
                        file.write(response.content)
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
    _wl = {s.strip() for s in config.free_whitelist if s.strip()}
    is_whitelisted = str(call.from_user.id) in _wl
    if is_whitelisted:
        requires_payment = False
        item_price = 0
    else:
        if premium_mode:
            requires_payment = True
            item_price = config.stars_premium_price
        else:
            requires_payment = (format == "audio") or (note in ("720p", "1080p"))
            item_price = config.stars_price

    if call.message:
        try:
            await call.message.delete()
        except Exception:
            pass

    if requires_payment:
        await state.update_data(purchase={
            'type': 'audio' if format == 'audio' else 'video',
            'format': format,
            'size': int(size),
            'note': note,
            'link': link,
            'domain': domain,
            'video_path': video_path,
            'thumbnail_path': thumbnail_path,
            'title': (data['title'] if data else title),
        })
        suffix = ":prem" if premium_mode else ""
        item_title = ("YouTube Audio" if format == "audio" else f"YouTube {note or 'video'}") + (" • Premium" if premium_mode else "")
        prices = [LabeledPrice(label=item_title, amount=item_price)]
        payload = f"yt:{'audio' if format == 'audio' else 'video'}:{format}:{call.from_user.id}{suffix}"
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
                prices=prices
            )
        except Exception as e:
            if call.message:
                await call.message.answer("Couldn't create invoice. Please try again later.")
            else:
                await call.answer("Couldn't create invoice. Open the bot in PM and try again.", show_alert=True)
            print(f"Invoice error: {e}")
        return

    db.set_work(call.from_user.id, 1)
    if call.message:
        await call.message.answer("Download started")
    else:
        await call.answer("Download started. I'll send it to you in PM.")
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

    if format != "audio":
        title_for_send = data['title'] if data else title
        my_thread = threading.Thread(target=simple_downloader, args=(link, video_path, target_chat_id, domain, format, title_for_send, thumbnail_path, None, call.from_user.id, sess_id))
        my_thread.start()
    else:
        audio_path = f"downloads/{title}.mp3"
        bot_info = await call.bot.get_me()
        bot_username = bot_info.username
        my_thread = threading.Thread(target=download_audio, args=(link, audio_path, target_chat_id, thumbnail_path, bot_username, None, call.from_user.id, sess_id))
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
            info_dict, ytlog = get_video_formats(link, domain)
            live = info_dict.get('is_live', False)
            if live:
                await message.answer("Live streams are restricted!")
                return
            title_orig = info_dict.get('title', 'No name')

            if domain.find("soundcloud.com") > -1:
                await message.answer("Download started")
                title = sanitize_filename(title_orig)
                audio_path = f"downloads/{title}.mp3"
                try:
                    thumb = info_dict['thumbnails'][7]['url']
                except:
                    thumb = info_dict['thumbnails'][-1]['url']
                thumbnail_path = video_path.replace("mp4", "jpg")
                response = requests.get(thumb)
                with open(thumbnail_path, 'wb') as file:
                    file.write(response.content)
                bot_info = await message.bot.get_me()
                bot_username = bot_info.username

                my_thread = threading.Thread(target=download_audio, args=(link, audio_path, message.chat.id, thumbnail_path, bot_username, None, message.from_user.id, str(message.chat.id)))
                my_thread.start()
                return
            elif domain.find("youtu") > -1:
                formats = info_dict.get('formats', [])
                if info_dict['live_status'] == 'is_live':
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
                        resp = requests.get(thumbnail_url, timeout=10)
                        if resp.ok and resp.content:
                            with open(thumbnail_path, 'wb') as file:
                                file.write(resp.content)
                            thumb_saved = True
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
                _wl = {s.strip() for s in config.free_whitelist if s.strip()}
                is_whitelisted = str(message.from_user.id) in _wl
                free_user = is_whitelisted
                force_paid = premium_mode and (not is_whitelisted)
                price_for_buttons = config.stars_premium_price if premium_mode else config.stars_price
                kb = youtube_formats_kb(formats, free=free_user, force_paid=force_paid, price=price_for_buttons)
                caption_text = title
                if premium_mode:
                    caption_text += f"\n\nNote: This video is age-restricted (18+) or has limited access on YouTube and is only accessible with cookies. All download options require {config.stars_premium_price} ⭐."
                # Send formats keyboard and also show yt-dlp stderr logs (if any)
                try:
                    if thumb_saved and os.path.exists(thumbnail_path):
                        await message.answer_photo(FSInputFile(thumbnail_path), caption_text, reply_markup=kb)
                    else:
                        await message.answer(caption_text, reply_markup=kb)
                except Exception:
                    await message.answer(caption_text, reply_markup=kb)
                # Show yt-dlp logs (warnings/errors) to the user to explain format probing
                try:
                    if ytlog and ytlog.strip():
                        # keep message short: send as a code block with limited size
                        log_text = ytlog.strip()
                        if len(log_text) > 1900:
                            log_text = log_text[-1900:]
                        await message.answer(f"<code>{log_text}</code>", parse_mode=ParseMode.HTML)
                except Exception:
                    pass
            else:
                if domain.find("tiktok") > -1 or domain.find("instagram") > -1 or domain.find("pinterest") > -1 or domain.find("vk.com") > -1:
                    db.set_work(message.from_user.id, 1)
                    await message.answer("Download started")
                    my_thread = threading.Thread(target=simple_downloader, args=(link, video_path, message.chat.id, domain, None, title_orig, None, None, message.from_user.id, str(message.chat.id)))
                    my_thread.start()
                else:
                    await message.answer(start_msg, reply_markup=remove_kb(), disable_web_page_preview=True)
        else:
            await message.answer(start_msg, reply_markup=remove_kb(), disable_web_page_preview=True)
    except:
        print(traceback.format_exc())
        await message.answer(start_msg, reply_markup=remove_kb(), disable_web_page_preview=True)

async def all(message: Message, state: FSMContext):
    try:
        chat_type = getattr(message.chat, 'type', 'private')
        text = message.text or ""
        import re as _re
        m = _re.search(r'(https?://\S+)', text)
        link = m.group(1) if m else None
        if not link:
            if chat_type in ("group", "supergroup"):
                return
            await message.answer(start_msg, reply_markup=remove_kb(), disable_web_page_preview=True)
            return
        domain = get_domain(link)
        if not is_supported_domain(domain):
            if chat_type in ("group", "supergroup"):
                return
            else:
                await message.answer(start_msg, reply_markup=remove_kb(), disable_web_page_preview=True)
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
            await message.answer(start_msg, reply_markup=remove_kb(), disable_web_page_preview=True)


async def pre_checkout_handler(pre_checkout_q: PreCheckoutQuery):
    try:
        await pre_checkout_q.bot.answer_pre_checkout_query(pre_checkout_q.id, ok=True)
    except Exception as e:
        print(f"PreCheckout answer error: {e}")


async def on_successful_payment(message: Message, state: FSMContext):
    sp = message.successful_payment
    if not sp:
        return
    payload = sp.invoice_payload or ""
    charge_id = getattr(sp, 'telegram_payment_charge_id', None)
    try:
        data = await state.get_data()
        purchase = data.get('purchase')
        if not purchase:
            await message.answer("Couldn't find your order. Please send the link again.")
            return
        try:
            if charge_id:
                db.add_payment(user_id=message.from_user.id, payload=payload, charge_id=charge_id)
        except Exception as e:
            print(f"Failed to save payment: {e}")
        link = purchase['link']
        domain = purchase['domain']
        video_path = purchase['video_path']
        thumbnail_path = purchase['thumbnail_path']
        title = sanitize_filename(purchase['title'])
        fmt = purchase['format']
        # Use payload from Telegram payment as the source of truth; fallback to state if needed
        purchase_payload = payload or data.get('purchase_payload')

        db.set_work(message.from_user.id, 1)
        await message.answer("Payment received ✅\nStarting download…")
        if fmt != 'audio':
            t = threading.Thread(target=simple_downloader, args=(link, video_path, message.chat.id, domain, fmt, purchase['title'], thumbnail_path, purchase_payload, message.from_user.id, str(message.chat.id)))
            t.start()
        else:
            audio_path = f"downloads/{title}.mp3"
            bot_info = await message.bot.get_me()
            bot_username = bot_info.username
            t = threading.Thread(target=download_audio, args=(link, audio_path, message.chat.id, thumbnail_path, bot_username, purchase_payload, message.from_user.id, str(message.chat.id)))
            t.start()
        await state.update_data(purchase=None)
        await state.update_data(purchase_payload=None)
    except Exception as e:
        print(f"Successful payment handling error: {e}")
        try:
            kb = None
            if payload:
                pay_price = config.stars_premium_price if (":prem" in str(payload)) else config.stars_price
                kb = AioInlineKeyboardMarkup(inline_keyboard=[[AioInlineKeyboardButton(text=f"🔄 Refund {pay_price}⭐", callback_data=f"refund:{payload}")]])
            await message.answer("Payment processing error. You can request a refund.", reply_markup=kb)
        except Exception:
            await message.answer("Payment processing error. Please try again.")


async def refund_star_payment(bot_token: str, user_id: int, charge_id: str) -> bool:
    url = f"https://api.telegram.org/bot{bot_token}/refundStarPayment"
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


async def refund_handler(call: CallbackQuery):
    try:
        _, payload = call.data.split(":", 1)
    except Exception:
        await call.answer("Invalid request", show_alert=True)
        return
    rec = db.get_payment_by_payload(payload)
    if not rec:
        await call.answer("Payment not found", show_alert=True)
        return
    _id, user_id, _payload, charge_id, status = rec
    if user_id != call.from_user.id:
        await call.answer("This is not your payment", show_alert=True)
        return
    if status == 'refunded':
        await call.answer("Already refunded", show_alert=True)
        return
    ok = await refund_star_payment(config.bot_token, user_id, charge_id)
    if ok:
        db.mark_payment_refunded(payload)
        try:
            await call.message.edit_text("Refund completed ✅")
        except Exception:
            await call.answer("Refund completed ✅", show_alert=True)
    else:
        await call.answer("Couldn't refund ⭐. Please try again later.", show_alert=True)


async def start_mail(message: Message, state: FSMContext):
    if str(message.from_user.id) not in config.admin_list:
        return
    await message.answer("Send a message to forward to all users\n/cancel to cancel.")
    await state.set_state(CatchMessageState.message)

async def confirm_mail(message: Message, state: FSMContext):
    await state.clear()
    if message.text == "/cancel":
        await message.answer("❌ Denied!")
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
    try:
        db.add_deeplink(token, link)
    except Exception:
        pass
    deeplink = f"https://t.me/{bot_username}?start={token}"
    pm_kb = AioInlineKeyboardMarkup(inline_keyboard=[[AioInlineKeyboardButton(text='Open bot to download', url=deeplink)]])

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
                await call.message.answer(start_msg, reply_markup=remove_kb(), disable_web_page_preview=True)
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
                await call.message.answer(start_msg, reply_markup=remove_kb(), disable_web_page_preview=True)
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

async def main():
    db.reset_work()
    clear_downloads()
    # Ensure dlp folder has the two latest yt-dlp releases before bot starts
    try:
        dlp_manager.download_latest_releases(2)
    except Exception as e:
        print(f"dlp_manager error: {e}")
    bot_properties = DefaultBotProperties(parse_mode=ParseMode.HTML)
    bot = Bot(token=bot_token, default=bot_properties)
    dp = Dispatcher(storage=MemoryStorage())
    dp.message.middleware(ExistsUserMiddleware())
    dp.message.middleware(ThrottlingMiddleware())

    dp.message.register(welcome, Command(commands="start"))
    dp.message.register(start_mail, Command(commands="mail"))
    dp.message.register(confirm_mail, CatchMessageState.message)
    dp.callback_query.register(mailer, F.data.startswith("mailer"))
    dp.callback_query.register(youtube_download, F.data.startswith("youtube_download"))
    dp.callback_query.register(refund_handler, F.data.startswith("refund:"))
    dp.pre_checkout_query.register(pre_checkout_handler)
    dp.message.register(on_successful_payment, F.successful_payment)
    dp.callback_query.register(check_subscription, F.data == "check_subscription")
    dp.inline_query.register(inline_query_handler)
    dp.message.register(all)

    print("Bot started")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

