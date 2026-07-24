from dotenv import load_dotenv
import os

# Load .env from the project directory (next to this file), regardless of current working directory.
_dotenv_path = os.path.join(os.path.dirname(__file__), ".env")
load_dotenv(dotenv_path=_dotenv_path, override=True)
bot_token = os.getenv('BOT_TOKEN')
# Optional: local/self-hosted Telegram Bot API server (e.g. http://132.243.225.48:6767/).
# Leave empty to use the official https://api.telegram.org.
bot_api_url = os.getenv('BOT_API_URL', '').strip().rstrip('/')
# Base URL for all Bot API HTTP requests (raw aiohttp calls and aiogram session)
telegram_api_base = bot_api_url or 'https://api.telegram.org'
channel_id = int(os.getenv('CHANNEL_ID')) if os.getenv('CHANNEL_ID') else None
channel_link = os.getenv('CHANNEL_LINK')
channel_name = os.getenv('CHANNEL_NAME', 'Subscribe to channel')
extra_channel_links = [link.strip() for link in os.getenv('EXTRA_CHANNEL_LINKS', '').split(',')] if os.getenv('EXTRA_CHANNEL_LINKS') else []
extra_channel_names = [name.strip() for name in os.getenv('EXTRA_CHANNEL_NAMES', '').split(',')] if os.getenv('EXTRA_CHANNEL_NAMES') else []
# No longer required (Pyrogram removed) — kept optional for backward compatibility
api_id = int(os.getenv('API_ID')) if os.getenv('API_ID') else None
api_hash = os.getenv('API_HASH')
admin_list = os.getenv('ADMIN_LIST').split(",")
stars_price = int(os.getenv('STARS_PRICE', '1'))
stars_premium_price = int(os.getenv('STARS_PREMIUM_PRICE', '5'))
# Paid downloads for non-YouTube services (SoundCloud/TikTok/Instagram/Pinterest/VK/X).
# Enabled by default. Set PAID_OTHER_SERVICES=0 to keep them free.
paid_other_services = os.getenv('PAID_OTHER_SERVICES', '1').lower() in ('1', 'true', 'yes')
# If not set separately, use the regular STARS_PRICE.
other_services_stars_price = int(os.getenv('OTHER_SERVICES_STARS_PRICE', str(stars_price)))
free_whitelist = os.getenv('FREE_WHITELIST', '').split(',')
# IDs (comma-separated) for which link logging to LOG_CHAT is skipped
no_log_whitelist = os.getenv('NO_LOG_WHITELIST', '').split(',')
# Chat ID for logging user link requests (optional)
log_chat = int(os.getenv('LOG_CHAT')) if os.getenv('LOG_CHAT') else None
# Control whether to stream yt-dlp output to console (default: True)
show_yt_dlp_output = os.getenv('SHOW_YT_DLP_OUTPUT', '1').lower() in ('1', 'true', 'yes')

# Optional: explicitly point to a yt-dlp executable path. If set, this overrides the dlp/ selection.
yt_dlp_executable = os.getenv('YTDLP_EXECUTABLE') or None

# Preferred platform for yt-dlp executable: 'windows', 'linux', or 'auto'
# If 'auto', the runtime platform will be used to pick the executable from dlp/ (or fallback to PATH)
yt_dlp_platform = os.getenv('YTDLP_PLATFORM', 'auto').lower()

# yt-dlp tuning knobs.
#
# EJS/YouTube JS challenge solving: point yt-dlp at a JS runtime.
# Examples:
#   YTDLP_JS_RUNTIMES=node
#   YTDLP_JS_RUNTIMES=node:C:\\Program Files\\nodejs\\node.exe
#   YTDLP_REMOTE_COMPONENTS=ejs:github
yt_dlp_js_runtimes = os.getenv('YTDLP_JS_RUNTIMES', '').strip()
yt_dlp_remote_components = os.getenv('YTDLP_REMOTE_COMPONENTS', '').strip()

# Optional proxy for all yt-dlp requests.
# Examples:
#   YTDLP_PROXY_URL=http://angel:wjOr04eN7SU4X2y@185.107.74.112:8080
#   YTDLP_PROXY_URL=socks5://angel:wjOr04eN7SU4X2y@185.107.74.112:1080
# Leave empty to disable proxy completely.
yt_dlp_proxy_url = os.getenv('YTDLP_PROXY_URL', '').strip()

# YouTube extractor args.
# Default uses `tv` client to avoid PO Token issues (web/android/ios often require PO tokens and/or SABR).
# You can override per your needs.
# Examples:
#   YTDLP_YOUTUBE_CLIENTS=tv
#   YTDLP_YOUTUBE_CLIENTS=web_safari
#   YTDLP_YOUTUBE_EXTRACTOR_ARGS=player_skip=webpage
yt_dlp_youtube_clients = os.getenv('YTDLP_YOUTUBE_CLIENTS', '').strip()
yt_dlp_youtube_extractor_args = os.getenv('YTDLP_YOUTUBE_EXTRACTOR_ARGS', '').strip()

# Optional: run extra `yt-dlp --list-formats` for console/file logging.
# This is useful for debugging, but costs extra CPU/time per request.
yt_dlp_log_list_formats = os.getenv('YTDLP_LOG_LIST_FORMATS', '0').lower() in ('1', 'true', 'yes')

# Optional direct image URL used as /start photo (sent with start text as caption)
start_photo_url = os.getenv('START_PHOTO_URL') or None

# Optional: Crypto Bot invoice URL for donations (shown on /start as a button)
# Example: https://t.me/CryptoBot?start=invoice-<id>
crypto_donate_invoice_url = os.getenv('CRYPTO_DONATE_INVOICE_URL') or None
