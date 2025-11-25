import asyncio
import os
import traceback
import datetime
from aiogram.types import FSInputFile
import config
from downloader import clear_downloads
try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None


def _now_msk():
    try:
        if ZoneInfo:
            return datetime.datetime.now(ZoneInfo("Europe/Moscow"))
    except Exception:
        pass
    # Fallback: UTC+3 approximation
    return datetime.datetime.utcnow() + datetime.timedelta(hours=3)


async def _send_db_to_chat(bot, chat_id):
    db_path = os.path.join("base", "db.db")
    if not os.path.exists(db_path):
        print(f"Scheduler: database file not found at {db_path}")
        return
    try:
        now = _now_msk()
        fname = now.strftime("db_%Y-%m-%d_%H-%M_MSK.db")
        await bot.send_document(
            chat_id=chat_id,
            document=FSInputFile(db_path, filename=fname),
            caption=f"DB backup • {now.strftime('%Y-%m-%d %H:%M')} MSK"
        )
        print(f"Scheduler: sent db to chat {chat_id}")
    except Exception as e:
        print(f"Scheduler: failed to send db to chat {chat_id}: {e}")


async def run_backup_scheduler(bot):
    """Every 3 hours send base/db.db to LOG_CHAT, if configured."""
    await asyncio.sleep(2)  # let bot finish startup
    interval = 3 * 60 * 60
    while True:
        try:
            chat_id = getattr(config, 'log_chat', None)
            if chat_id:
                await _send_db_to_chat(bot, chat_id)
            else:
                print("Scheduler: LOG_CHAT is not set; skipping backup send")
            # Clear downloads folder after sending backup
            try:
                clear_downloads()
                print("Scheduler: cleared downloads folder")
            except Exception as ce:
                print(f"Scheduler: failed to clear downloads: {ce}")
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            print("Scheduler: 3h backup scheduler cancelled")
            break
        except Exception:
            print("Scheduler: 3h backup scheduler error:\n", traceback.format_exc())
            await asyncio.sleep(60)
