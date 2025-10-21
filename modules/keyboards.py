from aiogram import types
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types.reply_keyboard_remove import ReplyKeyboardRemove
import config


def sub_kb():
    keyboard_builder = InlineKeyboardBuilder()
    keyboard_builder.button(text="Subscribe to channel", url=config.channel_link)
    keyboard_builder.button(text="Check subscription", callback_data="check_subscription")
    keyboard_builder.adjust(1)
    return keyboard_builder.as_markup()

def remove_kb():
    return ReplyKeyboardRemove()

def youtube_formats_kb(formats):
    keyboard_builder = InlineKeyboardBuilder()
    for f in formats:
        try:
            form_note = f.get('format_note', 'N/A')
            if f['ext'] == "mp4" and form_note not in ["N/A", "Default", "Premium"] and f['filesize']:
                if int(form_note[:-1]) <= 1080:
                    format_id = f['format_id']
                    size = f['filesize']
                    keyboard_builder.button(text=form_note, callback_data=f"youtube_download:{format_id}:{size}")
        except:
            pass
    keyboard_builder.adjust(6)
    keyboard_builder.row(
        types.InlineKeyboardButton(text="🎧 Аудио", callback_data="youtube_download:audio:0"),
    )
    return keyboard_builder.as_markup()

def confirm_mail_kb():
    keyboard_builder = InlineKeyboardBuilder()
    keyboard_builder.button(text="Да", callback_data=f"mailer:1")
    keyboard_builder.button(text="Нет", callback_data=f"mailer:0")
    keyboard_builder.adjust(2)
    return keyboard_builder.as_markup()
