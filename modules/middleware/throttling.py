from aiogram import BaseMiddleware
from cachetools import TTLCache


class ThrottlingMiddleware(BaseMiddleware):
    def __init__(self, time_limit=1):
        self.limit = TTLCache(maxsize=10_000, ttl=time_limit)

    async def __call__(self, handler, event, data):
        chat = getattr(event, 'chat', None)
        chat_type = getattr(chat, 'type', None)

        if chat_type in ("group", "supergroup"):
            return await handler(event, data)

        from_user = getattr(event, 'from_user', None)
        user_id = getattr(from_user, 'id', None)
        key = user_id or (getattr(chat, 'id', None))

        if key is None:
            return await handler(event, data)

        if key in self.limit:
            return
        self.limit[key] = None
        return await handler(event, data)
