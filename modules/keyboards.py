from __future__ import annotations

from aiogram import types
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import ReplyKeyboardRemove
import config

# Bot API 9.4+
# https://core.telegram.org/bots/api#inlinekeyboardbutton

# === ТВОИ custom_emoji_id ===
EMOJI = {
    "subscribe": "5242628160297641831",  # 🔔
    "check": "5427009714745517609",      # ✅
    "rocket": "5283080528818360566",     # 🚀
    "gem": "5280922999241859582",        # 💎
    "p1080": "5280769763398671636",      # 🏆
    "p720": "5431449001532594346",       # ⚡️
    "audio": "5435953773686043487",      # 🎧
    "no": "5465665476971471368",         # ❌
    "ban": "5334530826820398405",        # 🔨
}


def quality_height(note) -> int | None:
    """Height in pixels behind a quality note like "720p"/"640p"/720. None for audio."""
    if note is None:
        return None
    text = str(note).strip().lower()
    if not text or text == "audio":
        return None
    try:
        return int(text.rstrip("p"))
    except ValueError:
        return None


def is_paid_quality(note) -> bool:
    """True for any video quality above the free ceiling (FREE_MAX_HEIGHT, 480p).

    Matching by height instead of a "720p"/"1080p" whitelist keeps odd
    resolutions (640p, 854p, 1024p) from slipping through as free.
    """
    height = quality_height(note)
    if height is None:
        return False
    return height > int(getattr(config, "free_max_height", 480) or 0)


def tg_emoji_html(emoji_id: str, fallback: str) -> str:
    return f"<tg-emoji emoji-id=\"{emoji_id}\">{fallback}</tg-emoji>"


def tge(key: str, fallback: str) -> str:
    emoji_id = EMOJI.get(key)
    if not emoji_id:
        return fallback
    return tg_emoji_html(emoji_id, fallback)


def _ikb(
    text: str,
    *,
    callback_data: str | None = None,
    url: str | None = None,
    style: str | None = None,
    icon_custom_emoji_id: str | None = None,
) -> types.InlineKeyboardButton:

    kwargs: dict = {}
    if callback_data:
        kwargs["callback_data"] = callback_data
    if url:
        kwargs["url"] = url
    if style:
        kwargs["style"] = style
    if icon_custom_emoji_id:
        kwargs["icon_custom_emoji_id"] = icon_custom_emoji_id

    try:
        return types.InlineKeyboardButton(text=text, **kwargs)
    except TypeError:
        # fallback если aiogram старый
        kwargs.pop("style", None)
        kwargs.pop("icon_custom_emoji_id", None)
        return types.InlineKeyboardButton(text=text, **kwargs)


# Public helper (used across the project)
def ikb(
    text: str,
    *,
    callback_data: str | None = None,
    url: str | None = None,
    style: str | None = None,
    icon_custom_emoji_id: str | None = None,
) -> types.InlineKeyboardButton:
    return _ikb(
        text,
        callback_data=callback_data,
        url=url,
        style=style,
        icon_custom_emoji_id=icon_custom_emoji_id,
    )


def sub_kb():
    kb = InlineKeyboardBuilder()
    if config.channel_link:
        kb.row(
            _ikb(
                config.channel_name,
                url=config.channel_link,
                style="primary",
                icon_custom_emoji_id=EMOJI["subscribe"],
            )
        )
    
    for i, link in enumerate(config.extra_channel_links):
        name = config.extra_channel_names[i] if i < len(config.extra_channel_names) else f"Channel {i+1}"
        kb.row(
            _ikb(
                name,
                url=link,
                style="primary",
                icon_custom_emoji_id=EMOJI["subscribe"],
            )
        )

    kb.row(
        _ikb(
            "Check subscription",
            callback_data="check_subscription",
            style="success",
            icon_custom_emoji_id=EMOJI["check"],
        )
    )
    return kb.as_markup()


def remove_kb():
    return ReplyKeyboardRemove()


def start_kb(is_admin: bool = False) -> types.ReplyKeyboardRemove | types.InlineKeyboardMarkup:
    """Start keyboard.

    Shows the balance button when the balance is enabled, a donate button when
    CRYPTO_DONATE_INVOICE_URL is set, and an admin entry for admins only.
    Otherwise, keeps previous behavior (remove reply keyboard).
    """

    donate_url = getattr(config, "crypto_donate_invoice_url", None)
    balance_on = getattr(config, "balance_enabled", False)
    if not donate_url and not balance_on and not is_admin:
        return remove_kb()

    kb = InlineKeyboardBuilder()
    if balance_on:
        kb.row(
            _ikb(
                "My balance",
                callback_data="balance:show",
                style="primary",
                icon_custom_emoji_id=EMOJI.get("gem"),
            )
        )
    if donate_url:
        kb.row(
            _ikb(
                "Donate (Crypto Bot)",
                url=donate_url,
                style="success",
                icon_custom_emoji_id=EMOJI.get("gem"),
            )
        )
    if is_admin:
        kb.row(
            _ikb(
                "Admin panel",
                callback_data="admin:home",
                style="danger",
                icon_custom_emoji_id=EMOJI.get("ban"),
            )
        )
    return kb.as_markup()


def admin_kb() -> types.InlineKeyboardMarkup:
    """Main admin panel."""
    kb = InlineKeyboardBuilder()
    kb.row(
        _ikb("Broadcast", callback_data="admin:mail", style="primary",
             icon_custom_emoji_id=EMOJI.get("subscribe")),
        _ikb("Set balance", callback_data="admin:balance", style="primary",
             icon_custom_emoji_id=EMOJI.get("gem")),
    )
    kb.row(
        _ikb("Ban user", callback_data="admin:ban", style="danger",
             icon_custom_emoji_id=EMOJI.get("ban")),
        _ikb("Refund", callback_data="admin:refund",
             icon_custom_emoji_id=EMOJI.get("rocket")),
    )
    kb.row(
        _ikb("Stats", callback_data="admin:stats", style="success",
             icon_custom_emoji_id=EMOJI.get("check")),
    )
    kb.row(_ikb("Close", callback_data="admin:close", icon_custom_emoji_id=EMOJI.get("no")))
    return kb.as_markup()


def admin_back_kb(cancel: bool = False) -> types.InlineKeyboardMarkup:
    """Return-to-panel button; `cancel` labels it for an open prompt."""
    kb = InlineKeyboardBuilder()
    kb.row(
        _ikb(
            "Cancel" if cancel else "Back",
            callback_data="admin:home",
            style="danger" if cancel else None,
            icon_custom_emoji_id=EMOJI.get("no"),
        )
    )
    return kb.as_markup()


def balance_kb(options: list[int] | None = None) -> types.InlineKeyboardMarkup:
    """Top-up amounts, two per row."""
    amounts = options if options is not None else getattr(config, "stars_topup_options", [])
    kb = InlineKeyboardBuilder()

    row: list[types.InlineKeyboardButton] = []
    for amount in amounts:
        row.append(
            _ikb(
                f"{amount} ⭐",
                callback_data=f"topup:{amount}",
                style="primary",
                icon_custom_emoji_id=EMOJI.get("gem"),
            )
        )
        if len(row) == 2:
            kb.row(*row)
            row = []
    if row:
        kb.row(*row)

    kb.row(
        _ikb(
            "Close",
            callback_data="balance:close",
            icon_custom_emoji_id=EMOJI.get("no"),
        )
    )
    return kb.as_markup()


def cancel_download_btn(download_id: str, *, text: str = "Cancel download") -> types.InlineKeyboardButton:
    return _ikb(
        text,
        callback_data=f"cancel_download:{download_id}",
        style="danger",
        icon_custom_emoji_id=EMOJI.get("no"),
    )


def cancel_download_kb(download_id: str, *, text: str = "Cancel download") -> types.InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(cancel_download_btn(download_id, text=text))
    return kb.as_markup()


def youtube_formats_kb(
    formats,
    free: bool = True,
    force_paid: bool = False,
    price: int | None = None,
    token: str | None = None,
):
    kb = InlineKeyboardBuilder()
    best_by_note: dict[str, dict] = {}

    def label_for(base_text: str, *, is_audio: bool = False) -> str:
        effective_price = price if price is not None else config.stars_price
        if force_paid and effective_price > 0:
            return f"{base_text} ({effective_price}⭐)"
        if free:
            return base_text
        if (is_audio or is_paid_quality(base_text)) and config.stars_price > 0:
            return f"{base_text} ({config.stars_price}⭐)"
        return base_text

    def note_for(f: dict) -> str | None:
        note = f.get("format_note")
        if isinstance(note, str) and note and note not in ("N/A", "Default", "Premium"):
            if quality_height(note) is not None:
                return note

        h = f.get("height")
        if h:
            return f"{int(h)}p"

        return None

    for f in formats:
        try:
            if f.get("ext") != "mp4":
                continue
            if f.get("vcodec") == "images":
                continue

            note = note_for(f)
            if not note:
                continue

            if (quality_height(note) or 0) > 1080:
                continue

            size = int(f.get("filesize") or f.get("filesize_approx") or 0)

            current = best_by_note.get(note)
            if not current or size > int(current.get("_size_for_btn", 0) or 0):
                best_by_note[note] = {**f, "_size_for_btn": size}

        except Exception:
            continue

    suffix = f":{token}" if token else ""

    if not best_by_note:
        kb.row(
            _ikb(
                label_for("Audio", is_audio=True),
                callback_data=f"youtube_download:audio:0:audio{suffix}",
                style="primary",
                icon_custom_emoji_id=EMOJI["audio"],
            )
        )
        return kb.as_markup()

    sorted_notes = sorted(best_by_note.items(), key=lambda kv: quality_height(kv[0]) or 0)

    rows: list[tuple[str, str, str | None]] = []
    for note, f in sorted_notes:
        format_id = f["format_id"]
        size = int(f.get("_size_for_btn", 0) or 0)
        icon = None
        if note == "1080p":
            icon = EMOJI.get("p1080")
        elif note == "720p":
            icon = EMOJI.get("p720")
        rows.append((label_for(note), f"youtube_download:{format_id}:{size}:{note}{suffix}", icon))

    rows.append((label_for("Audio", is_audio=True), f"youtube_download:audio:0:audio{suffix}", EMOJI.get("audio")))

    n = len(rows)
    red_rows: set[int] = set()
    if n == 2:
        red_rows = {1}
    elif n >= 3:
        top = n // 3
        bottom = n // 3
        if top == 0:
            top = 1
        if bottom == 0:
            bottom = 1
        if top + bottom >= n:
            top = max(1, n - 1)
            bottom = 1

        red_start = top
        red_end = n - bottom
        red_rows = set(range(red_start, red_end))

    for idx, (text, cb, icon) in enumerate(rows):
        style = "danger" if idx in red_rows else None
        kb.row(_ikb(text, callback_data=cb, style=style, icon_custom_emoji_id=icon))

    kb.row(
        _ikb(
            "Cancel",
            callback_data="delete_formats_msg",
            icon_custom_emoji_id=EMOJI.get("no"),
        )
    )

    return kb.as_markup()


def confirm_mail_kb():
    kb = InlineKeyboardBuilder()
    kb.row(
        _ikb(
            "Yes",
            callback_data="mailer:1",
            style="success",
            icon_custom_emoji_id=EMOJI["check"],
        ),
        _ikb(
            "No",
            callback_data="mailer:0",
            style="danger",
            icon_custom_emoji_id=EMOJI["no"],
        ),
    )
    return kb.as_markup()


def ban_kb(user_id: int):
    kb = InlineKeyboardBuilder()
    kb.row(
        _ikb(
            "BAN",
            callback_data=f"ban:{user_id}",
            style="danger",
            icon_custom_emoji_id=EMOJI["ban"],
        )
    )
    return kb.as_markup()
