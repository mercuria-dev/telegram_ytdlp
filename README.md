# YtDlp Telegram Bot

![Баннер](https://i.ibb.co/nwnrB9H/icon.png)

# 📖 Description
This bot can download:
- Photos and videos from Instagram, Tik Tok.
- Videos (with quality selection) and audio (in the best quality) from YouTube.
- Music from SoundCloud.

# ⚙️ Project Setup Guide

Welcome to the project setup guide! Follow these steps to configure and run the project using Python.

## Getting Started

### 1. Configure Cookies

To enhance the scraping of YouTube and Instagram, you need to provide Netscape cookies. Place your cookies in the following files:
- `cookies/insta.txt` for Instagram
- `cookies/youtube.txt` for YouTube

### 2. Create and Configure Your Telegram Channel

1. **Create a Telegram Channel:** 
   You need a Telegram channel to make your bot popular. People can use your bot by joining this channel.

2. **Add Your Bot to the Channel:**
   - Create a Telegram bot.
   - Add the bot to your newly created channel.

3. **Create a Telegram App:**
   - Go to [Telegram API Development Tools](https://my.telegram.org/apps).
   - Create your app and get your `API_ID` and `API_HASH`.

### 3. Configure Environment Variables

Add the following information to your `.env` file (the bot is fully free — no payments):

```env
BOT_TOKEN=your_telegram_bot_token
CHANNEL_ID=your_channel_id (ex. -100123123123)
CHANNEL_LINK=your_channel_link (ex. t.me/***)
API_ID=your_telegram_app_api_id
API_HASH=your_telegram_app_api_hash
ADMIN_LIST=123,456   # telegram user IDs of admins
# Optional logging chat for admin logs and backups
LOG_CHAT=your_log_chat_id (ex. -100987654321)
# Optional: do not log links for these user IDs in LOG_CHAT
NO_LOG_WHITELIST=123,456 # telegram user IDs
```

## Setup Instructions

1. **Install dependencies**

   Install the required Python packages from `requirements.txt`:

   ```cmd
   pip install -r requirements.txt
   ```

2. **Run the bot**

   Start the bot with Python:

   ```cmd
   python main.py
   ```

## Notes

- Ensure that your `.env` file is properly configured with the correct values.
- If `LOG_CHAT` is set, the bot will:
   - Log each processed link with a ❌BAN button for quick moderation.
   - Send automatic database backups (`base/db.db`) to the log chat every 3 hours with a timestamped filename.

# 🚀 Usage

Type /start then paste your link to video on youtube, instagram, tik tok etc

If you are in admin list, type /admin. There is a message sender. It sends your message to all users in your bot!

