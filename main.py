import asyncio
from aiogram import Bot, Dispatcher, F, types
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message, CallbackQuery
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
import requests
import random
import config

db = DataBase()
with open("start.txt", "rt", encoding="utf-8") as start_file:
    start_msg = start_file.read()
    start_file.close()

async def welcome(message: Message):
    await message.answer(start_msg, reply_markup=remove_kb(), disable_web_page_preview=True)

async def youtube_download(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    _, work = db.get_user(call.from_user.id)
    if work == 1:
        await call.message.answer("Wait while your video is downloading")
        return

    if not data:
        await call.answer("Send me link again")
        return
    link = data['link']
    domain = data['domain']
    video_path = data['video_path']
    thumbnail_path = data['thumbnail_path']
    title = sanitize_filename(data['title'])
    parts = call.data.split(":")
    # Expected: youtube_download:format_id:size:note OR youtube_download:audio:0:audio
    _, format, size = parts[:3]
    note = parts[3] if len(parts) > 3 else None
    max_size = 2 * 1024 * 1024 * 1024
    if int(size) >= max_size:
        await call.message.answer("File is too large. Try another")
        return 
    # Determine if this selection requires payment (Stars for 720p, 1080p, and audio)
    _wl = {s.strip() for s in config.free_whitelist if s.strip()}
    is_whitelisted = str(call.from_user.id) in _wl
    requires_payment = (not is_whitelisted) and ((format == "audio") or (note in ("720p", "1080p")))

    if requires_payment:
        # Store the pending purchase details in state for fulfillment upon payment
        await state.update_data(purchase={
            'type': 'audio' if format == 'audio' else 'video',
            'format': format,
            'size': int(size),
            'note': note,
            'link': link,
            'domain': domain,
            'video_path': video_path,
            'thumbnail_path': thumbnail_path,
            'title': data['title'],
        })
        item_title = "YouTube Audio" if format == "audio" else f"YouTube {note or 'video'}"
        prices = [LabeledPrice(label=item_title, amount=config.stars_price)]
        payload = f"yt:{'audio' if format == 'audio' else 'video'}:{format}:{call.from_user.id}"
        await state.update_data(purchase_payload=payload)
        try:
            await call.message.answer_invoice(
                title=item_title,
                description=f"Pay {config.stars_price} ⭐ to download",
                payload=payload,
                provider_token=None,  # Not required for Telegram Stars
                currency="XTR",
                prices=prices
            )
        except Exception as e:
            await call.message.answer("Couldn't create invoice. Please try again later.")
            print(f"Invoice error: {e}")
        return

    # Free download path for other qualities
    db.set_work(call.from_user.id, 1)
    await call.message.answer("Download started")
    if format != "audio":
        my_thread = threading.Thread(target=simple_downloader, args=(link, video_path, call.from_user.id, domain, format, data['title'], thumbnail_path))
        my_thread.start()
    else:
        audio_path = f"downloads/{title}.mp3"
        bot_info = await call.bot.get_me()
        bot_username = bot_info.username
        my_thread = threading.Thread(target=download_audio, args=(link, audio_path, call.from_user.id, thumbnail_path, bot_username,))
        my_thread.start()

async def all(message: Message, state: FSMContext):
    try:
        link = message.text
        domain = get_domain(link)
        if domain:
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
            info_dict = get_video_formats(link, domain)
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

                my_thread = threading.Thread(target=download_audio, args=(link, audio_path, message.from_user.id, thumbnail_path, bot_username,))
                my_thread.start()
                return
            elif domain.find("youtu") > -1:
                formats = info_dict.get('formats', [])
                if info_dict['live_status'] == 'is_live':
                    await message.answer("Live streams are restricted!")
                    return
                # Formats logs
                '''
                for f in formats:
                    print(f"Format code: {f['format_id']}, Extension: {f['ext']}, "
                        f"Resolution: {f.get('resolution', 'N/A')}, "
                        f"Note: {f.get('format_note', 'N/A')}, "
                        f"Filesize: {f.get('filesize', 'N/A')}")'''

                thumbnail_url = info_dict.get('thumbnail', None)
                thumbnail_path = video_path.replace("mp4", "jpg")
                response = requests.get(thumbnail_url)
                with open(thumbnail_path, 'wb') as file:
                    file.write(response.content)
                title = info_dict.get('title', 'No name')
                await state.update_data(link=link)
                await state.update_data(title=title)
                await state.update_data(domain=domain)
                await state.update_data(video_path=video_path)
                await state.update_data(thumbnail_path=thumbnail_path)
                # Show free labels for whitelisted users
                _wl = {s.strip() for s in config.free_whitelist if s.strip()}
                free_user = str(message.from_user.id) in _wl
                kb = youtube_formats_kb(formats, free=free_user)
                if not thumbnail_url:
                    await message.answer(title, reply_markup=kb)
                else:
                    await message.answer_photo(thumbnail_url, title, reply_markup=kb)
            else:
                if domain.find("tiktok") > -1 or domain.find("instagram") > -1 or domain.find("pinterest") > -1 or domain.find("vk.com") > -1:
                    db.set_work(message.from_user.id, 1)
                    await message.answer("Download started")
                    my_thread = threading.Thread(target=simple_downloader, args=(link, video_path, message.from_user.id, domain, None, title_orig,))
                    my_thread.start()
                else:
                    await message.answer(start_msg, reply_markup=remove_kb(), disable_web_page_preview=True)
        else:
            await message.answer(start_msg, reply_markup=remove_kb(), disable_web_page_preview=True)
    except:
        await message.answer(start_msg, reply_markup=remove_kb(), disable_web_page_preview=True)


async def pre_checkout_handler(pre_checkout_q: PreCheckoutQuery):
    # Required: answer pre-checkout query
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
        # Save payment info for potential refunds
        try:
            if charge_id:
                db.add_payment(user_id=message.from_user.id, payload=payload, charge_id=charge_id)
        except Exception as e:
            print(f"Failed to save payment: {e}")
        # Start the actual download now
        link = purchase['link']
        domain = purchase['domain']
        video_path = purchase['video_path']
        thumbnail_path = purchase['thumbnail_path']
        title = sanitize_filename(purchase['title'])
        fmt = purchase['format']
        purchase_payload = data.get('purchase_payload')

        db.set_work(message.from_user.id, 1)
        await message.answer("Payment received ✅\nStarting download…")
        if fmt != 'audio':
            t = threading.Thread(target=simple_downloader, args=(link, video_path, message.from_user.id, domain, fmt, purchase['title'], thumbnail_path, purchase_payload))
            t.start()
        else:
            audio_path = f"downloads/{title}.mp3"
            bot_info = await message.bot.get_me()
            bot_username = bot_info.username
            t = threading.Thread(target=download_audio, args=(link, audio_path, message.from_user.id, thumbnail_path, bot_username, purchase_payload))
            t.start()
        # Clear purchase from state
        await state.update_data(purchase=None)
        await state.update_data(purchase_payload=None)
    except Exception as e:
        print(f"Successful payment handling error: {e}")
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


async def check_subscription(call: CallbackQuery):
    try:
        user_id = call.from_user.id
        ch_id = config.channel_id
        member = await call.bot.get_chat_member(chat_id=ch_id, user_id=user_id)
        status = member.status
        if status in ["member", "administrator", "creator"]:
            await call.message.delete()
            await call.message.answer(start_msg, reply_markup=remove_kb(), disable_web_page_preview=True)
        else:
            await call.answer("Subscribe to the channel to use the bot", show_alert=True)
    except Exception as e:
        await call.answer("Failed to check subscription. Please try again later.", show_alert=True)
        print(f"check_subscription error: {e}")

async def main():
    db.reset_work()
    clear_downloads()
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
    # Payments handlers
    dp.pre_checkout_query.register(pre_checkout_handler)
    dp.message.register(on_successful_payment, F.successful_payment)
    dp.callback_query.register(check_subscription, F.data == "check_subscription")
    dp.message.register(all)

    print("Bot started")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

