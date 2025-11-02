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

def youtube_formats_kb(formats, free: bool = False):
    keyboard_builder = InlineKeyboardBuilder()
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
            try:
                res_val = int(form_note.rstrip('p'))
            except Exception:
                continue
            if res_val > 1080:
                continue
            current = best_by_note.get(form_note)
            if not current or (f.get('filesize', 0) > current.get('filesize', 0)):
                best_by_note[form_note] = f
        except Exception:
            continue

    sorted_notes = sorted(best_by_note.items(), key=lambda kv: int(kv[0].rstrip('p')))
    btn_720 = None
    btn_1080 = None
    # Add all buttons except 720p/1080p first
    for note, f in sorted_notes:
        format_id = f['format_id']
        size = f.get('filesize', 0)
        if note == "720p":
            label = "720p" if free else f"720p ({config.stars_price}⭐)"
            btn_720 = types.InlineKeyboardButton(text=label, callback_data=f"youtube_download:{format_id}:{size}:{note}")
            continue
        if note == "1080p":
            label = "1080p" if free else f"1080p ({config.stars_price}⭐)"
            btn_1080 = types.InlineKeyboardButton(text=label, callback_data=f"youtube_download:{format_id}:{size}:{note}")
            continue
        keyboard_builder.button(text=note, callback_data=f"youtube_download:{format_id}:{size}:{note}")

    # Arrange the non-paid buttons in rows
    keyboard_builder.adjust(6)
    # Put 720p and 1080p each on a new line if they exist
    if btn_720 is not None:
        keyboard_builder.row(btn_720)
    if btn_1080 is not None:
        keyboard_builder.row(btn_1080)
    # Audio row
    keyboard_builder.row(
        types.InlineKeyboardButton(text=("🎧 Audio" if free else f"🎧 Audio ({config.stars_price}⭐)"), callback_data="youtube_download:audio:0:audio"),
    )
    return keyboard_builder.as_markup()

def confirm_mail_kb():
    keyboard_builder = InlineKeyboardBuilder()
    keyboard_builder.button(text="Yes", callback_data=f"mailer:1")
    keyboard_builder.button(text="No", callback_data=f"mailer:0")
    keyboard_builder.adjust(2)
    return keyboard_builder.as_markup()
