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
    # Group formats by visible note (resolution) and pick the one with the largest filesize
    best_by_note = {}
    for f in formats:
        try:
            form_note = f.get('format_note', 'N/A')
            if f['ext'] != "mp4":
                continue
            if form_note in ["N/A", "Default", "Premium"]:
                continue
            if not f.get('filesize'):
                continue
            # parse numeric part of note like '720p'
            try:
                res_val = int(form_note.rstrip('p'))
            except Exception:
                continue
            if res_val > 1080:
                continue
            # Choose the format with the largest filesize for this note
            current = best_by_note.get(form_note)
            if not current or (f.get('filesize', 0) > current.get('filesize', 0)):
                best_by_note[form_note] = f
        except Exception:
            continue

    # Sort notes by resolution ascending (so keyboard looks ordered)
    sorted_notes = sorted(best_by_note.items(), key=lambda kv: int(kv[0].rstrip('p')))
    for note, f in sorted_notes:
        format_id = f['format_id']
        size = f.get('filesize', 0)
        keyboard_builder.button(text=note, callback_data=f"youtube_download:{format_id}:{size}")

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
