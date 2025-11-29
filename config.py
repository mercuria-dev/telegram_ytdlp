from dotenv import load_dotenv
import os

load_dotenv()
bot_token = os.getenv('BOT_TOKEN')
channel_id = int(os.getenv('CHANNEL_ID'))
channel_link = os.getenv('CHANNEL_LINK')
api_id = int(os.getenv('API_ID'))
api_hash = os.getenv('API_HASH')
admin_list = os.getenv('ADMIN_LIST').split(",")
stars_price = int(os.getenv('STARS_PRICE', '1'))
stars_premium_price = int(os.getenv('STARS_PREMIUM_PRICE', '5'))
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
