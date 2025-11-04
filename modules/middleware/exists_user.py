# - *- coding: utf- 8 - *-
from aiogram import BaseMiddleware
from modules.database import DataBase
from modules.keyboards import sub_kb
import config

db = DataBase()

class ExistsUserMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user = getattr(event, 'from_user', None)
        user_id = getattr(user, 'id', None)
        if user_id is None:
            # System event without user, just pass through
            return await handler(event, data)

        # Ensure user exists in DB
        if not db.get_user(user_id):
            db.add_user(user_id)

        # Determine chat type (if available)
        chat = getattr(event, 'chat', None)
        if chat is None:
            # For CallbackQuery: chat is on message
            msg = getattr(event, 'message', None)
            chat = getattr(msg, 'chat', None)
        chat_type = getattr(chat, 'type', None)

        # Skip subscription checks in group/supergroup and for events without chat (e.g., inline)
        if chat_type in ("group", "supergroup") or chat_type is None:
            return await handler(event, data)

        # Only enforce in private chats
        channel_id = getattr(config, 'channel_id', None)
        if not channel_id:
            return await handler(event, data)

        try:
            user_status = await event.bot.get_chat_member(chat_id=channel_id, user_id=user_id)
        except Exception:
            # If check fails, don't block usage
            return await handler(event, data)

        if user_status.status == "kicked":
            # Blocked from channel; silently ignore
            return

        if user_status.status != "left":
            return await handler(event, data)

        # Not subscribed: show prompt only in the same private chat, no DM spam from group events
        try:
            await event.bot.send_message(
                chat_id=chat.id,
                text="To activate the bot, please subscribe to the channel.",
                reply_markup=sub_kb()
            )
        except Exception:
            pass
        return
