import asyncio
from aiogram import Bot, Dispatcher, F, types
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message, CallbackQuery
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
    _, format, size = call.data.split(":")
    max_size = 2 * 1024 * 1024 * 1024
    if int(size) >= max_size:
        await call.message.answer("File is too large. Try another")
        return 
    db.set_work(call.from_user.id, 1)
    await call.message.answer("Downloading has been started")
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
                await message.answer("Live streams is restricted!")
                return
            title_orig = info_dict.get('title', 'No name')

            if domain.find("soundcloud.com") > -1:
                await message.answer("Downloading has been started")
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
                    await message.answer("Live streams is restricted!")
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
                kb = youtube_formats_kb(formats)
                if not thumbnail_url:
                    await message.answer(title, reply_markup=kb)
                else:
                    await message.answer_photo(thumbnail_url, title, reply_markup=kb)
            else:
                if domain.find("tiktok") > -1 or domain.find("instagram") > -1 or domain.find("pinterest") > -1 or domain.find("vk.com") > -1:
                    db.set_work(message.from_user.id, 1)
                    await message.answer("Downloading has been started")
                    my_thread = threading.Thread(target=simple_downloader, args=(link, video_path, message.from_user.id, domain, None, title_orig,))
                    my_thread.start()
                else:
                    await message.answer(start_msg, reply_markup=remove_kb(), disable_web_page_preview=True)
        else:
            await message.answer(start_msg, reply_markup=remove_kb(), disable_web_page_preview=True)
    except:
        await message.answer(start_msg, reply_markup=remove_kb(), disable_web_page_preview=True)


async def start_mail(message: Message, state: FSMContext):
    if str(message.from_user.id) not in config.admin_list:
        return
    await message.answer("Send message forwarding to other users\n/cancel - use to cansel operation")
    await state.set_state(CatchMessageState.message)

async def confirm_mail(message: Message, state: FSMContext):
    await state.clear()
    if message.text == "/cancel":
        await message.answer("❌Denied!")
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
        await call.message.answer("Canseled")
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
            await call.answer("Subscribe to channel for using bot", show_alert=True)
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
    dp.callback_query.register(check_subscription, F.data == "check_subscription")
    dp.message.register(all)

    print("Bot started")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

