# - *- coding: utf- 8 - *-
import config

OK = "ok"
BANNED = "banned"
NOT_SUBSCRIBED = "not_subscribed"


async def channel_status(bot, user_id: int) -> str:
    """User's standing in the required channel.

    No channel configured or a failed lookup counts as OK, so a Telegram hiccup
    never locks people out of the bot.
    """
    channel_id = getattr(config, 'channel_id', None)
    if not channel_id:
        return OK
    try:
        member = await bot.get_chat_member(chat_id=channel_id, user_id=user_id)
    except Exception:
        return OK
    if member.status == "kicked":
        return BANNED
    if member.status == "left":
        return NOT_SUBSCRIBED
    return OK
