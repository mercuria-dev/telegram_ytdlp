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

def youtube_formats_kb(formats, free: bool = False, force_paid: bool = False, price: int | None = None):
    keyboard_builder = InlineKeyboardBuilder()
    best_by_note = {}

    def note_for(f):
        note = f.get('format_note')
        if isinstance(note, str) and note and note not in ("N/A", "Default", "Premium"):
            try:
                int(note.rstrip('p'))
                return note
            except Exception:
                pass
        h = f.get('height')
        if not h:
            res = f.get('resolution') or ''
            try:
                h = int(str(res).split('x')[-1]) if 'x' in str(res) else None
            except Exception:
                h = None
        if h:
            return f"{int(h)}p"
        return None

    for f in formats:
        try:
            if f.get('ext') != "mp4":
                continue
            if f.get('vcodec') == 'images':
                continue
            note = note_for(f)
            if not note:
                continue
            try:
                if int(note.rstrip('p')) > 1080:
                    continue
            except Exception:
                continue

            size = f.get('filesize') or f.get('filesize_approx') or 0
            current = best_by_note.get(note)
            if not current:
                best_by_note[note] = {
                    **f,
                    '_size_for_btn': int(size or 0),
                }
            else:
                cur_size = current.get('_size_for_btn', 0)
                new_size = int(size or 0)
                if new_size > cur_size:
                    best_by_note[note] = {
                        **f,
                        '_size_for_btn': new_size,
                    }
        except Exception:
            continue

    if not best_by_note:
        keyboard_builder.row(
            types.InlineKeyboardButton(text=("🎧 Audio" if free else f"🎧 Audio ({config.stars_price}⭐)"), callback_data="youtube_download:audio:0:audio"),
        )
        return keyboard_builder.as_markup()

    sorted_notes = sorted(best_by_note.items(), key=lambda kv: int(kv[0].rstrip('p')))
    btn_720 = None
    btn_1080 = None
    for note, f in sorted_notes:
        format_id = f['format_id']
        size = int(f.get('_size_for_btn', 0) or 0)
        if note == "720p":
            if force_paid:
                label = f"720p ({(price or config.stars_price)}⭐)"
            else:
                label = "720p" if free else f"720p ({config.stars_price}⭐)"
            btn_720 = types.InlineKeyboardButton(text=label, callback_data=f"youtube_download:{format_id}:{size}:{note}")
            continue
        if note == "1080p":
            if force_paid:
                label = f"1080p ({(price or config.stars_price)}⭐)"
            else:
                label = "1080p" if free else f"1080p ({config.stars_price}⭐)"
            btn_1080 = types.InlineKeyboardButton(text=label, callback_data=f"youtube_download:{format_id}:{size}:{note}")
            continue
        if force_paid:
            keyboard_builder.button(text=f"{note} ({(price or config.stars_price)}⭐)", callback_data=f"youtube_download:{format_id}:{size}:{note}")
        else:
            keyboard_builder.button(text=note, callback_data=f"youtube_download:{format_id}:{size}:{note}")

    keyboard_builder.adjust(6)
    if btn_720 is not None:
        keyboard_builder.row(btn_720)
    if btn_1080 is not None:
        keyboard_builder.row(btn_1080)
    if force_paid:
        keyboard_builder.row(
            types.InlineKeyboardButton(text=f"🎧 Audio ({(price or config.stars_price)}⭐)", callback_data="youtube_download:audio:0:audio"),
        )
    else:
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
