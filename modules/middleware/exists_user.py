# - *- coding: utf- 8 - *-
from aiogram import BaseMiddleware
from modules.database import DataBase
from modules.keyboards import sub_kb, ban_kb
import config

db = DataBase()

class ExistsUserMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user = getattr(event, 'from_user', None)
        user_id = getattr(user, 'id', None)
        if user_id is None:
            return await handler(event, data)

        if not db.get_user(user_id):
            db.add_user(user_id)
            # Log new user if log chat configured
            if getattr(config, 'log_chat', None):
                try:
                    first_name = getattr(user, 'first_name', 'User')
                    mention = f"<a href='tg://user?id={user_id}'>{first_name}</a>"
                    await event.bot.send_message(chat_id=config.log_chat,
                                                 text=f"New user <code>{user_id}</code> {mention}",
                                                 reply_markup=ban_kb(user_id),
                                                 disable_web_page_preview=True)
                except Exception as e:
                    print(f"New user log error: {e}")

        chat = getattr(event, 'chat', None)
        if chat is None:
            msg = getattr(event, 'message', None)
            chat = getattr(msg, 'chat', None)
        chat_type = getattr(chat, 'type', None)

        if chat_type in ("group", "supergroup") or chat_type is None:
            return await handler(event, data)

        channel_id = getattr(config, 'channel_id', None)
        if not channel_id:
            return await handler(event, data)

        try:
            user_status = await event.bot.get_chat_member(chat_id=channel_id, user_id=user_id)
        except Exception:
            return await handler(event, data)

        if user_status.status == "kicked":
            return

        if user_status.status != "left":
            return await handler(event, data)

        try:
            await event.bot.send_message(
                chat_id=chat.id,
                text="To activate the bot, please subscribe to the channel.",
                reply_markup=sub_kb()
            )
        except Exception:
            pass
        return
