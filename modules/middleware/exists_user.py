# - *- coding: utf- 8 - *-
from aiogram import BaseMiddleware
from modules.database import DataBase
from modules.keyboards import sub_kb, ban_kb
from modules import subscription
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

        # Groups are checked only right before a download starts (see main.py),
        # so ordinary chat messages there never trigger a channel lookup.
        status = await subscription.channel_status(event.bot, user_id)
        if status == subscription.BANNED:
            return
        if status == subscription.OK:
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
