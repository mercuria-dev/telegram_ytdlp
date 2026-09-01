# YtDlp Telegram Bot

![Banner](https://i.ibb.co/nwnrB9H/icon.png)

# 📖 Description

Telegram bot that downloads media via [yt-dlp](https://github.com/yt-dlp/yt-dlp):

- **YouTube** — video (with quality selection) and audio (best quality). Playlist/mix links are ignored.
- **Instagram**, **TikTok**, **Pinterest** — photos and videos.
- **X / Twitter** — photos and videos.
- **VK / VK Video** — videos and clips.
- **SoundCloud** — music.

Features:

- Auto-downloads the latest `yt-dlp` binaries into `dlp/` on startup (stable or nightly channel).
- Optional channel-subscription gate (`CHANNEL_ID`) with extra "subscribe" buttons.
- Optional Telegram Stars payments (separate prices for regular / age-restricted YouTube / other services).
- Optional star balance: top up once (10/50/100/200/500/1000 ⭐) and paid downloads are debited from it; without a balance they are billed per download as before. Failed or cancelled downloads are refunded automatically either way.
- Optional self-hosted Telegram Bot API server for uploads up to 2 GB.
- Inline admin panel (`/admin`): broadcast, set a user's balance, ban, refund, stats. Link logging with a ban button, automatic DB backups.
- Inline mode (`@your_bot <link>`), `/cancel` for active downloads.

# ⚙️ Setup

## 1. Install

```bash
git clone https://github.com/mercuria-dev/telegram_ytdlp
cd telegram_ytdlp
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

Python 3.11+ (tested up to 3.14). `yt-dlp` itself is downloaded automatically into `dlp/` on first start — no need to install it.

## 2. Create the bot

1. Create a bot via [@BotFather](https://t.me/BotFather) and copy the token → `BOT_TOKEN`.
2. (Optional) Create a channel, add the bot to it as admin, and put its ID into `CHANNEL_ID` — users will have to subscribe before using the bot.
3. Get your own Telegram user ID (e.g. via [@userinfobot](https://t.me/userinfobot)) → `ADMIN_LIST`.

## 3. Cookies (optional, recommended)

Export cookies in **Netscape format** (e.g. with the "Get cookies.txt LOCALLY" browser extension) and put them here:

| File                  | Service                                           |
| --------------------- | ------------------------------------------------- |
| `cookies/youtube.txt` | YouTube (age-restricted / private / limited-access videos) |
| `cookies/insta.txt`   | Instagram                                         |
| `cookies/tiktok.txt`  | TikTok                                            |
| `cookies/twitter.txt` | X / Twitter                                       |

Missing files are simply skipped. The `cookies/` folder is git-ignored.

## 4. Configure `.env`

Create a `.env` file in the project root (next to `config.py`). Each line is `NAME=value`, no quotes, no spaces around `=`. Lines starting with `#` are comments. Lists are comma-separated without spaces: `ADMIN_LIST=123,456`.

All variables are listed below. Only **Required** ones are needed to start; everything else has a default.

### Core

| Variable       | Required | Default | What to put there |
| -------------- | -------- | ------- | ----------------- |
| `BOT_TOKEN`    | ✅ yes   | —       | Bot token from @BotFather: `123456:ABC-DEF...` |
| `ADMIN_LIST`   | ✅ yes   | —       | Telegram user IDs of admins: `123,456`. Admins can use `/mail` and `/dorefund`. |
| `BOT_API_URL`  | no       | `https://api.telegram.org` | URL of a self-hosted Bot API server, e.g. `http://127.0.0.1:6767`. Enables uploads up to 2 GB. See [Local Bot API server](#local-bot-api-server). Leave empty to use the official API (50 MB upload limit). |

### Channel subscription gate

| Variable              | Required | Default                | What to put there |
| --------------------- | -------- | ---------------------- | ----------------- |
| `CHANNEL_ID`          | no       | — (gate disabled)      | Channel ID the user must be subscribed to: `-1001234567890`. The bot must be an admin of the channel. |
| `CHANNEL_LINK`        | no       | —                      | Link shown on the subscribe button: `https://t.me/your_channel` |
| `CHANNEL_NAME`        | no       | `Subscribe to channel` | Text of the subscribe button |
| `EXTRA_CHANNEL_LINKS` | no       | —                      | Extra links shown as additional buttons (not checked): `https://t.me/one,https://t.me/two` |
| `EXTRA_CHANNEL_NAMES` | no       | `Channel 1`, `Channel 2`, … | Button texts for `EXTRA_CHANNEL_LINKS`, same order: `News,Chat` |

### Logging & backups

| Variable           | Required | Default | What to put there |
| ------------------ | -------- | ------- | ----------------- |
| `LOG_CHAT`         | no       | — (off) | Chat/channel ID for logs: `-100987654321`. The bot must be able to post there. When set, every processed link is logged with a ❌BAN button, and `base/db.db` is sent there every 3 hours. |
| `NO_LOG_WHITELIST` | no       | —       | User IDs whose links are **not** logged: `123,456` |

### Telegram Stars payments

| Variable                     | Required | Default              | What to put there |
| ---------------------------- | -------- | -------------------- | ----------------- |
| `STARS_PRICE`                | no       | `1`                  | Price in ⭐ for a regular YouTube download. `0` = free. |
| `STARS_PREMIUM_PRICE`        | no       | `5`                  | Price in ⭐ for age-restricted (18+) / limited-access YouTube videos (requires `cookies/youtube.txt`). `0` = free. |
| `PAID_OTHER_SERVICES`        | no       | `1`                  | `1` — charge Stars for Instagram/TikTok/Pinterest/VK/X/SoundCloud; `0` — keep them free. |
| `OTHER_SERVICES_STARS_PRICE` | no       | same as `STARS_PRICE` | Price in ⭐ for non-YouTube services |
| `FREE_WHITELIST`             | no       | —                    | User IDs that never pay: `123,456` |
| `BALANCE_ENABLED`            | no       | `1`                  | `1` — offer an optional star balance and debit paid downloads from it when it covers the price; `0` — hide it and invoice every download. |
| `STARS_TOPUP_OPTIONS`        | no       | `10,50,100,200,500,1000` | Top-up amounts in ⭐ offered as buttons under `/balance`. |

**How the balance works.** The balance is optional and never blocks anything. When a download costs stars the bot tries the balance first: if it covers the price, the stars are debited and the download starts right away with no invoice to confirm. If the balance can't cover it, Telegram bills that single download exactly as it did before — the user is not asked to top up. `/balance` (or the *My balance* button on `/start`) is where topping up lives.

**Refunds are automatic.** A download that fails, is cancelled, or dies with the bot gives the stars back on its own: to the balance if that is what paid, otherwise through Telegram's `refundStarPayment`. A manual refund button is only attached when the automatic refund could not go through. A top-up itself can be refunded in full as long as it hasn't been spent yet.

Set `BALANCE_ENABLED=0` to hide the balance entirely and bill every download through Telegram.

### /start appearance

| Variable                    | Required | Default | What to put there |
| --------------------------- | -------- | ------- | ----------------- |
| `START_PHOTO_URL`           | no       | —       | Direct image URL sent with `/start` (start text becomes the caption): `https://example.com/banner.jpg` |
| `CRYPTO_DONATE_INVOICE_URL` | no       | —       | [Crypto Bot](https://t.me/CryptoBot) invoice link shown as a "Donate" button: `https://t.me/CryptoBot?start=invoice-xxxx` |

### yt-dlp

| Variable                       | Required | Default  | What to put there |
| ------------------------------ | -------- | -------- | ----------------- |
| `YTDLP_CHANNEL`                | no       | `stable` | `stable` or `nightly`. Nightly builds get YouTube extractor fixes days/weeks earlier. |
| `YTDLP_PLATFORM`               | no       | `auto`   | Which binary from `dlp/` to use: `auto`, `linux` or `windows` |
| `YTDLP_EXECUTABLE`             | no       | —        | Absolute path to your own `yt-dlp` binary. Overrides `dlp/` selection entirely. |
| `YTDLP_PROXY_URL`              | no       | —        | Proxy for all yt-dlp requests: `http://user:pass@127.0.0.1:8080` or `socks5://user:pass@127.0.0.1:1080` |
| `YTDLP_JS_RUNTIMES`            | no       | —        | JS runtime for YouTube challenge solving: `node` or `node:C:\Program Files\nodejs\node.exe` |
| `YTDLP_REMOTE_COMPONENTS`      | no       | —        | Passed to `--remote-components`, e.g. `ejs:github` |
| `YTDLP_YOUTUBE_CLIENTS`        | no       | `tv`     | YouTube player client(s): `tv`, `web_safari`, … |
| `YTDLP_YOUTUBE_EXTRACTOR_ARGS` | no       | —        | Extra YouTube extractor args: `player_skip=webpage` |
| `YTDLP_LOG_LIST_FORMATS`       | no       | `0`      | `1` — run an extra `--list-formats` per request and log it (debug only, slow) |
| `SHOW_YT_DLP_OUTPUT`           | no       | `1`      | `1` — stream yt-dlp output to console; `0` — silent |

### Local Bot API server only

| Variable   | Required | Default | What to put there |
| ---------- | -------- | ------- | ----------------- |
| `API_ID`   | no*      | —       | `api_id` from [my.telegram.org/apps](https://my.telegram.org/apps) |
| `API_HASH` | no*      | —       | `api_hash` from the same page |

\* Not used by the bot itself. Required only by `aiogram_server.sh` to start the self-hosted Bot API server.

### Minimal `.env` example

```env
BOT_TOKEN=123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11
ADMIN_LIST=123456789
```

### Full `.env` example

```env
# --- Core ---
BOT_TOKEN=123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11
ADMIN_LIST=123456789,987654321
BOT_API_URL=http://127.0.0.1:6767

# --- Channel gate ---
CHANNEL_ID=-1001234567890
CHANNEL_LINK=https://t.me/your_channel
CHANNEL_NAME=Subscribe to channel
EXTRA_CHANNEL_LINKS=https://t.me/one,https://t.me/two
EXTRA_CHANNEL_NAMES=News,Chat

# --- Logging & backups ---
LOG_CHAT=-100987654321
NO_LOG_WHITELIST=123456789

# --- Stars ---
STARS_PRICE=1
STARS_PREMIUM_PRICE=5
PAID_OTHER_SERVICES=1
OTHER_SERVICES_STARS_PRICE=1
FREE_WHITELIST=123456789
BALANCE_ENABLED=1
STARS_TOPUP_OPTIONS=10,50,100,200,500,1000

# --- /start ---
START_PHOTO_URL=https://example.com/banner.jpg
CRYPTO_DONATE_INVOICE_URL=https://t.me/CryptoBot?start=invoice-xxxx

# --- yt-dlp ---
YTDLP_CHANNEL=nightly
YTDLP_PROXY_URL=socks5://user:pass@127.0.0.1:1080
YTDLP_JS_RUNTIMES=node
YTDLP_REMOTE_COMPONENTS=ejs:github

# --- Local Bot API server (aiogram_server.sh) ---
API_ID=1234567
API_HASH=0123456789abcdef0123456789abcdef
```

## 5. Run

```bash
python main.py
```

Auto-restart wrappers that log every run into `logs/runs/` and record exit codes:

```bash
./restart_bot.sh        # Linux
restart_bot.bat         # Windows
```

Logs: `logs/bot.log` (rotating), `logs/restart.log`, `logs/runs/bot_<timestamp>.log`.

## Local Bot API server

The official Bot API limits uploads to 50 MB. A self-hosted server raises it to 2 GB.

1. Get `API_ID` / `API_HASH` at [my.telegram.org/apps](https://my.telegram.org/apps) and put them into `.env`.
2. Start the server (Docker, listens on `127.0.0.1:6767`):
   ```bash
   ./aiogram_server.sh
   ```
3. Set `BOT_API_URL=http://127.0.0.1:6767` in `.env` and restart the bot.

> Note: a bot token can be logged in to only one Bot API server at a time. If you move back to the official API, call `https://api.telegram.org/bot<TOKEN>/logOut` first.

# 🚀 Usage

**Users**

- `/start` — start, then paste a link.
- `/balance` (or `/topup`) — show your star balance and top it up by 10/50/100/200/500/1000 ⭐.
- `/cancel` — cancel your active downloads.
- Inline: `@your_bot <link>` in any chat.

**Admins** (IDs from `ADMIN_LIST`)

- `/admin` (or `/panel`, or the *Admin panel* button on `/start`) — inline admin panel:
  - **Broadcast** — send a message (text/photo/video/animation, formatting preserved) to every user, with a confirmation step.
  - **Set balance** — overwrite a user's star balance: send `user_id amount`, or `user_id` alone to look it up. The ledger records the difference, and the user is notified.
  - **Ban user** — send a `user_id` to ban them in `CHANNEL_ID`.
  - **Refund** — send a `payment_id` or `charge_id` to refund a Stars payment.
  - **Stats** — users, active downloads, balance holders, stars held and paid, refunds.

  Inside any prompt, `/cancel` or the *Cancel* button returns to the panel.

The equivalent commands still work directly:

- `/mail` — broadcast a message (text/media, HTML formatting preserved) to all users. Send `/cancel` to abort.
- `/dorefund <payment_id_or_charge_id>` — refund a Stars payment. Refunding a top-up takes the stars back off the balance first, and is refused if they've already been spent.
- `/addbalance <user_id> <amount>` — grant stars (a negative amount takes them away).
- `/getbalance <user_id>` — show a user's balance.
- ❌BAN button in `LOG_CHAT` — ban the user in the channel.
