
import os
import subprocess
import shutil
import signal
import random
import threading
import logging
from logging.handlers import RotatingFileHandler
from collections import deque
from pyrogram import Client, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import config
import asyncio
import re
import time
from modules.database import DataBase
from modules import dlp_manager

import shlex
import html
from PIL import Image
import json
import requests
from urllib.parse import quote, urlsplit, urlunsplit
from modules.keyboards import cancel_download_kb, tge

db = DataBase()


def _get_ytdlp_logger() -> logging.Logger:
    """Dedicated logger for yt-dlp output.

    Writes to logs/ytdlp.log with rotation. Does not spam root logger by default.
    """
    logger = logging.getLogger("telegram_ytdlp.ytdlp")
    if getattr(logger, "_configured", False):
        return logger

    logger.setLevel(logging.INFO)
    logger.propagate = False

    try:
        os.makedirs("logs", exist_ok=True)
        handler = RotatingFileHandler(
            "logs/ytdlp.log",
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        fmt = logging.Formatter(
            fmt="%(asctime)s %(levelname)s %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(fmt)
        handler.setLevel(logging.INFO)
        logger.addHandler(handler)
    except Exception:
        # If file handler fails, still return logger.
        pass

    logger._configured = True
    return logger


def _looks_like_netscape_cookiefile(path: str | None) -> bool:
    """Best-effort check for Netscape cookies.txt format.

    yt-dlp requires Netscape cookie file format. Users sometimes paste JSON here,
    which makes yt-dlp exit(1). If format is invalid, we skip cookies.
    """
    try:
        if not path or not os.path.exists(path):
            return False
        if os.path.getsize(path) <= 0:
            return False

        with open(path, 'rt', encoding='utf-8', errors='ignore') as f:
            # Read a small prefix; enough to detect JSON/HTML and the first cookie line.
            head = f.read(4096)

        s = (head or '').lstrip('\ufeff').lstrip()
        if not s:
            return False

        # Common wrong formats
        if s[0] in '{[':
            return False
        if s.lower().startswith('<!doctype') or s.lower().startswith('<html'):
            return False

        # Accept official header
        if '# netscape http cookie file' in s.lower():
            return True

        # Otherwise, look for at least one valid cookie line:
        # domain \t flag \t path \t secure \t expiration \t name \t value
        for line in s.splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            parts = line.split('\t')
            if len(parts) >= 7:
                return True
        return False
    except Exception:
        return False


def _cmd_to_str(cmd: list[str]) -> str:
    try:
        # Windows friendly
        if os.name == 'nt':
            return subprocess.list2cmdline(cmd)
        # Python 3.8+ has shlex.join
        import shlex as _shlex
        return _shlex.join(cmd)
    except Exception:
        return ' '.join(str(x) for x in cmd)


def _build_ytdlp_proxy_url() -> str | None:
    try:
        raw_proxy_url = (getattr(config, 'yt_dlp_proxy_url', None) or '').strip()
        if raw_proxy_url:
            return raw_proxy_url
        return None
    except Exception:
        return None


def _yt_dlp_proxy_args() -> list[str]:
    proxy_url = _build_ytdlp_proxy_url()
    if not proxy_url:
        return []
    return ['--proxy', proxy_url]


def _redact_proxy_url(proxy_url: str) -> str:
    try:
        parsed = urlsplit(proxy_url)
        if parsed.username is None and parsed.password is None:
            return proxy_url

        host = parsed.hostname or ''
        if ':' in host and not host.startswith('['):
            host = f"[{host}]"
        if parsed.port is not None:
            host = f"{host}:{parsed.port}"

        auth = quote(parsed.username or '', safe='')
        if parsed.password is not None:
            auth += ':***'
        if auth:
            host = f"{auth}@{host}"

        return urlunsplit((parsed.scheme, host, parsed.path, parsed.query, parsed.fragment))
    except Exception:
        if '@' not in proxy_url:
            return proxy_url
        prefix, suffix = proxy_url.rsplit('@', 1)
        if '://' not in prefix:
            return proxy_url
        scheme, creds = prefix.split('://', 1)
        if ':' in creds:
            user, _ = creds.split(':', 1)
            return f"{scheme}://{user}:***@{suffix}"
        return f"{scheme}://***@{suffix}"


def _sanitize_cmd_for_logging(cmd: list[str]) -> list[str]:
    sanitized: list[str] = []
    redact_next = False
    for arg in cmd:
        if redact_next:
            sanitized.append(_redact_proxy_url(arg))
            redact_next = False
            continue

        if arg == '--proxy':
            sanitized.append(arg)
            redact_next = True
            continue

        if arg.startswith('--proxy='):
            sanitized.append(f"--proxy={_redact_proxy_url(arg.split('=', 1)[1])}")
            continue

        sanitized.append(arg)
    return sanitized


def _rc_to_reason(rc: int) -> str:
    # On POSIX, negative return code means "killed by signal".
    if rc < 0:
        sig = -rc
        try:
            name = signal.Signals(sig).name
            return f"killed by signal {sig} ({name})"
        except Exception:
            return f"killed by signal {sig}"
    # In shells, an exit code >=128 often means signal (128+N)
    if rc >= 128:
        sig = rc - 128
        if 1 <= sig <= 255:
            try:
                name = signal.Signals(sig).name
                return f"exit {rc} (signal {sig} / {name})"
            except Exception:
                return f"exit {rc} (signal {sig})"
    return f"exit {rc}"


def _is_youtube(url_or_domain: str | None) -> bool:
    try:
        if not url_or_domain:
            return False
        s = str(url_or_domain).lower()
        if re.match(r'^https?://', s):
            d = get_domain(s) or ''
            return 'youtu' in d.lower()
        return 'youtu' in s
    except Exception:
        return False


def _youtube_extractor_args() -> list[str]:
    """Return yt-dlp CLI args to reduce YouTube EJS/n-challenge breakage."""
    try:
        clients = (getattr(config, 'yt_dlp_youtube_clients', None) or '').strip()
        extra = (getattr(config, 'yt_dlp_youtube_extractor_args', None) or '').strip()

        parts: list[str] = []
        if clients:
            parts.append(f"player_client={clients}")
        if extra:
            parts.append(extra.strip('; '))

        if not parts:
            return []
        return ['--extractor-args', f"youtube:{';'.join(parts)}"]
    except Exception:
        return []


def _yt_dlp_runtime_args() -> list[str]:
    """Return global yt-dlp args (JS runtime + remote EJS components), if configured."""
    try:
        out: list[str] = []
        out += _yt_dlp_proxy_args()
        jsr = (getattr(config, 'yt_dlp_js_runtimes', None) or '').strip()
        # Match plain `yt-dlp URL` behavior by default:
        # do NOT force a JS runtime unless explicitly configured.
        rc = (getattr(config, 'yt_dlp_remote_components', None) or '').strip()
        if jsr:
            out += ['--js-runtimes', jsr]
        if rc:
            out += ['--remote-components', rc]
        return out
    except Exception:
        return []


def run_yt_dlp_process(args_list, capture_output: bool = False, return_stderr: bool = False):
    # Prefer selected executable from dlp_manager (dlp/ folder). Falls back to system `yt-dlp`.
    exe = getattr(config, 'yt_dlp_executable', None) or dlp_manager.get_selected_executable() or 'yt-dlp'
    cmd = [exe] + args_list
    logger = _get_ytdlp_logger()
    cmd_str = _cmd_to_str(_sanitize_cmd_for_logging(cmd))
    if capture_output:
        # Always capture output when caller needs to parse it
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors='replace')
        if proc.returncode != 0:
            reason = _rc_to_reason(proc.returncode)
            stderr_txt = (proc.stderr or '').strip()
            stdout_txt = (proc.stdout or '').strip()
            # Keep the exception readable: include last lines only
            tail = '\n'.join((stdout_txt.splitlines() + stderr_txt.splitlines())[-30:])
            logger.error("yt-dlp failed (%s): %s\n%s", reason, cmd_str, tail)
            raise RuntimeError(stderr_txt or tail or f"yt-dlp failed ({reason})")
        # Also log stderr (sometimes contains warnings useful for debugging)
        if (proc.stderr or '').strip():
            logger.info("yt-dlp stderr: %s", (proc.stderr or '').strip()[-1000:])
        if return_stderr:
            return proc.stdout, proc.stderr
        return proc.stdout
    # When not capturing, always stream output to the console so operator sees progress/logs
    logger.info("yt-dlp start: %s", cmd_str)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors='replace')

    last_lines: deque[str] = deque(maxlen=50)
    if proc.stdout is not None:
        for line in proc.stdout:
            s = (line or '').rstrip('\n')
            if s:
                last_lines.append(s)
                try:
                    logger.info("%s", s)
                except Exception:
                    pass
                if getattr(config, 'show_yt_dlp_output', True):
                    try:
                        print(s)
                    except Exception:
                        pass

    proc.wait()
    if proc.returncode != 0:
        reason = _rc_to_reason(proc.returncode)
        tail = '\n'.join(list(last_lines)[-30:])
        logger.error("yt-dlp failed (%s): %s\nLast output:\n%s", reason, cmd_str, tail)
        raise RuntimeError(f"yt-dlp failed ({reason}). See logs/ytdlp.log")
    logger.info("yt-dlp done: %s", cmd_str)
    return None


def run_yt_dlp_process_with_pid(args_list, download_id: str = None):
    exe = getattr(config, 'yt_dlp_executable', None) or dlp_manager.get_selected_executable() or 'yt-dlp'
    cmd = [exe] + args_list
    logger = _get_ytdlp_logger()
    cmd_str = _cmd_to_str(_sanitize_cmd_for_logging(cmd))
    logger.info("yt-dlp start%s: %s", f" download_id={download_id}" if download_id else "", cmd_str)
    
    # Запускаем процесс с выводом в консоль
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors='replace')

    # Attach a log path to the process so callers that stream stdout can also tee into a file.
    try:
        os.makedirs("logs", exist_ok=True)
        ts = int(time.time())
        tag = download_id or f"pid{proc.pid or 'na'}"
        log_path = os.path.join("logs", f"yt-dlp_{tag}_{ts}.log")
        setattr(proc, "_ytdlp_log_path", log_path)
    except Exception:
        setattr(proc, "_ytdlp_log_path", None)
    
    # Сохраняем PID в базу данных, если передан download_id
    if download_id and proc.pid:
        try:
            db.update_download_pid(download_id, proc.pid)
        except Exception as e:
            print(f"Failed to save PID for download {download_id}: {e}")
    
    return proc


def cancel_download_process(download_id: str):
    try:
        pid = db.get_download_pid(download_id)
        if not pid:
            return False, "PID not found"
        
        try:
            if os.name == 'nt':  # Windows
                try:
                    subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'], 
                                 capture_output=True, timeout=5)
                except (subprocess.TimeoutExpired, FileNotFoundError):
                    try:
                        os.kill(pid, signal.SIGTERM)
                    except PermissionError:
                        try:
                            subprocess.run(['taskkill', '/PID', str(pid), '/T'], 
                                         capture_output=True, timeout=5)
                        except:
                            return False, "No permission to terminate process"
            else:  # Unix/Linux
                os.kill(pid, signal.SIGKILL)
            
            time.sleep(2)
            
            db.update_download_status(download_id, "cancelled")
            
            return True, "Download cancelled successfully"
        except ProcessLookupError:
            db.update_download_status(download_id, "cancelled")
            return True, "Process already terminated"
        except Exception as e:
            return False, f"Failed to cancel process: {str(e)}"
            
    except Exception as e:
        return False, f"Error cancelling download: {str(e)}"


def generate_download_id(user_id: int) -> str:
    timestamp = int(time.time())
    random_suffix = random.randint(1000, 9999)
    return f"{user_id}_{timestamp}_{random_suffix}"


def send_download_started_message(chat_id: int, download_id: str, url: str):
    try:
        keyboard = cancel_download_kb(download_id)

        # Send message via Bot API
        url_api = f"https://api.telegram.org/bot{config.bot_token}/sendMessage"
        data = {
            "chat_id": str(chat_id),
            "text": (
                f"{tge('rocket', '🚀')} Starting download...\n\n"
                f"Link: {html.escape(url[:100])}...\n\n"
                "You can cancel the download if you selected the wrong link."
            ),
            "parse_mode": "HTML",
            "reply_markup": json.dumps(keyboard.model_dump(by_alias=True, exclude_none=True)),
            "disable_web_page_preview": True
        }

        resp = requests.post(url_api, data=data, timeout=30)
        if resp.status_code == 200 and resp.json().get('ok'):
            return resp.json()['result']['message_id']
        return None
    except Exception as e:
        print(f"Failed to send download started message: {e}")
        return None


def update_download_message(chat_id: int, message_id: int, text: str):
    try:
        url_api = f"https://api.telegram.org/bot{config.bot_token}/editMessageText"
        data = {
            'chat_id': str(chat_id),
            'message_id': message_id,
            'text': text,
            'parse_mode': 'HTML',
            'disable_web_page_preview': 'true'
        }
        
        resp = requests.post(url_api, data=data, timeout=30)
        return resp.status_code == 200 and resp.json().get('ok')
    except Exception:
        return False


def delete_download_message(chat_id: int, message_id: int):
    try:
        url_api = f"https://api.telegram.org/bot{config.bot_token}/deleteMessage"
        data = {
            'chat_id': str(chat_id),
            'message_id': message_id
        }
        resp = requests.post(url_api, data=data, timeout=30)
        return resp.status_code == 200 and resp.json().get('ok')
    except Exception:
        return False


def get_info_json(url, cookiefile=None):
    args = ["--dump-single-json", "--no-warnings", "--no-progress"]
    if cookiefile:
        args = ["--cookies", cookiefile] + args
    args += _yt_dlp_runtime_args()
    if _is_youtube(url):
        args += _youtube_extractor_args()
    args += [url]
    out = run_yt_dlp_process(args, capture_output=True)
    try:
        return json.loads(out)
    except Exception as e:
        raise RuntimeError(f"Failed to parse yt-dlp JSON output: {e}\nOutput:\n{out}")


def select_cookiefile(url_or_domain: str | None) -> str | None:
    try:
        d = None
        if not url_or_domain:
            d = None
        else:
            # If it's a URL, resolve domain; else assume it's a domain string
            if re.match(r'^https?://', str(url_or_domain), re.IGNORECASE):
                d = get_domain(str(url_or_domain))
            else:
                d = str(url_or_domain)
        d = (d or '').lower()
        # Prefer site-specific cookies; fallback to YouTube cookies if present
        if 'youtu' in d:
            cand = 'cookies/youtube.txt'
            if os.path.exists(cand) and _looks_like_netscape_cookiefile(cand):
                return cand
            if os.path.exists(cand):
                try:
                    _get_ytdlp_logger().warning("Ignoring invalid cookies file (not Netscape): %s", cand)
                except Exception:
                    pass
            return None
        if 'instagram' in d:
            cand = 'cookies/insta.txt'
            if os.path.exists(cand) and _looks_like_netscape_cookiefile(cand):
                return cand
            if os.path.exists(cand):
                try:
                    _get_ytdlp_logger().warning("Ignoring invalid cookies file (not Netscape): %s", cand)
                except Exception:
                    pass
            return None
        if 'tiktok' in d:
            cand = 'cookies/tiktok.txt'
            if os.path.exists(cand) and _looks_like_netscape_cookiefile(cand):
                return cand
            if os.path.exists(cand):
                try:
                    _get_ytdlp_logger().warning("Ignoring invalid cookies file (not Netscape): %s", cand)
                except Exception:
                    pass
            return None
        # Fallback: use YouTube cookies if available (harmless for other domains)
        cand = 'cookies/youtube.txt'
        if os.path.exists(cand) and _looks_like_netscape_cookiefile(cand):
            return cand
        if os.path.exists(cand):
            try:
                _get_ytdlp_logger().warning("Ignoring invalid cookies file (not Netscape): %s", cand)
            except Exception:
                pass
        return None
    except Exception:
        return None

def bot_api_send_message(chat_id: int | str, text: str, payment_payload: str | None = None) -> bool:
    try:
        url = f"https://api.telegram.org/bot{config.bot_token}/sendMessage"
        data = {
            'chat_id': str(chat_id),
            'text': text,
            'disable_web_page_preview': 'true'
        }
        if payment_payload:
            pay_price = config.stars_premium_price if ':prem' in str(payment_payload) else config.stars_price
            reply_markup = {
                "inline_keyboard": [[
                    {
                        "text": f"🔄 Refund {pay_price}⭐",
                        "callback_data": f"refund:{payment_payload}",
                    }
                ]]
            }
            data['reply_markup'] = json.dumps(reply_markup)
        if len(data['text']) > 3500:
            data['text'] = data['text'][:3500] + "..."
        resp = requests.post(url, data=data, timeout=30)
        if resp.status_code != 200:
            return False
        j = resp.json()
        return bool(j.get('ok'))
    except Exception:
        return False

def clear_downloads():
    fs = os.listdir('downloads')
    for f in fs:
        delete_file(f"downloads/{f}")

def crop_to_square(image_path, out_path):
    with Image.open(image_path) as img:
        width, height = img.size
        new_size = min(width, height)
        left = (width - new_size) / 2
        top = (height - new_size) / 2
        right = (width + new_size) / 2
        bottom = (height + new_size) / 2
        img = img.crop((left, top, right, bottom))
        img.save(out_path)

def sanitize_filename(text):
    if not text:
        return ""
    sanitized_text = text.strip()
    sanitized_text = re.sub(r'[\\/:"*?<>|]+', '', sanitized_text)
    sanitized_text = re.sub(r'%+', '', sanitized_text)
    sanitized_text = re.sub(r'[\x00-\x1f]', '', sanitized_text)
    sanitized_text = re.sub(r'\s+', '_', sanitized_text)
    sanitized_text = re.sub(r'_+', '_', sanitized_text)
    return sanitized_text[:200]

def download_audio(video_url, output_path, chat_id, thumb, bot_username, payment_payload=None, user_id_for_work=None, session_id=None):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        if not output_path.lower().endswith('.mp3'):
            output_path = output_path + '.mp3'

        base_name = os.path.basename(output_path)
        base_no_ext = os.path.splitext(base_name)[0]
        safe_base = sanitize_filename(base_no_ext)
        outtmpl = os.path.join('downloads', safe_base)

        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': outtmpl,
            'writethumbnail': True,
            'noprogress': True,
            'quiet': True,
        }

        ck = select_cookiefile(video_url)

        max_retries = 3
        last_exc = None
        # Build base args
        args = []
        if ydl_opts.get('format'):
            args += ['-f', ydl_opts['format']]
        if ydl_opts.get('outtmpl'):
            args += ['-o', ydl_opts['outtmpl']]
        if ydl_opts.get('writethumbnail'):
            args += ['--write-thumbnail']
        # extract audio to mp3, embed thumbnail, add metadata
        args += ['--extract-audio', '--audio-format', 'mp3', '--audio-quality', '128', '--embed-thumbnail', '--add-metadata']
        # Always show progress/logs for yt-dlp (do not add quiet/no-progress)
        if ck:
            args = ['--cookies', ck] + args
        args += _yt_dlp_runtime_args()
        if _is_youtube(video_url):
            args += _youtube_extractor_args()

        for attempt in range(1, max_retries + 1):
            try:
                run_yt_dlp_process(args + [video_url])
                last_exc = None
                break
            except Exception as de:
                last_exc = de
                time.sleep(min(2 * attempt, 6))
                continue

        if last_exc is not None:
            # Prefer Bot API notification so we don't hit Pyrogram peer issues
            msg = str(last_exc)
            sent = bot_api_send_message(chat_id, f"Download failed after {max_retries} attempts: {msg}")
            if not sent:
                try:
                    session_base = f"sessions/{(session_id if session_id is not None else chat_id)}"
                    app = Client(session_base, bot_token=config.bot_token, api_id=config.api_id, api_hash=config.api_hash)
                    app.start()
                    app.send_message(chat_id=chat_id, text=f"Download failed after {max_retries} attempts: {msg}")
                    app.stop()
                    delete_pyrogram_session_files(session_base)
                except Exception:
                    pass
            db.set_work(user_id_for_work or chat_id, 0)
            delete_file(output_path)
            return

        possible_thumb = None
        for ext in ('.jpg', '.jpeg', '.webp', '.png'):
            candidate = outtmpl + ext
            if os.path.exists(candidate):
                possible_thumb = candidate
                break

        audio_thumb = None
        if possible_thumb:
            audio_thumb = outtmpl + '_audio.jpg'
            try:
                crop_to_square(possible_thumb, audio_thumb)
            except Exception:
                audio_thumb = possible_thumb
        else:
            if thumb and os.path.exists(thumb):
                audio_thumb = outtmpl + '_audio.jpg'
                try:
                    crop_to_square(thumb, audio_thumb)
                except Exception:
                    audio_thumb = thumb

        produced_mp3 = outtmpl + '.mp3'
        # Try Bot API upload for files <= 50MB, else fallback to Pyrogram
        try:
            size_bytes = os.path.getsize(produced_mp3)
        except Exception:
            size_bytes = 0

        if size_bytes and size_bytes <= 50 * 1024 * 1024:
            try:
                url = f"https://api.telegram.org/bot{config.bot_token}/sendAudio"
                data = {
                    'chat_id': str(chat_id),
                    'caption': f"{tge('gem', '💎')} <b><a href='https://t.me/{bot_username}'>@{bot_username}</a></b>",
                    'parse_mode': 'HTML',
                    'title': safe_base,
                }
                with open(produced_mp3, 'rb') as af:
                    files = {
                        'audio': (os.path.basename(produced_mp3), af)
                    }
                    if audio_thumb and os.path.exists(audio_thumb):
                        with open(audio_thumb, 'rb') as tf:
                            files['thumbnail'] = (os.path.basename(audio_thumb), tf)
                            resp = requests.post(url, data=data, files=files, timeout=120)
                    else:
                        resp = requests.post(url, data=data, files=files, timeout=120)
                if resp.status_code != 200 or not resp.json().get('ok', False):
                    raise RuntimeError(f"Bot API sendAudio failed: HTTP {resp.status_code} {resp.text[:200]}")
            except Exception as e:
                # Fallback to Pyrogram if Bot API fails
                try:
                    session_base = f"sessions/{(session_id if session_id is not None else chat_id)}"
                    app = Client(session_base, bot_token=config.bot_token, api_id=config.api_id, api_hash=config.api_hash)
                    app.start()
                    caption_html = f"{tge('gem', '💎')} <b><a href='https://t.me/{bot_username}'>@{bot_username}</a></b>"
                    caption_fallback = f"💎 <b><a href='https://t.me/{bot_username}'>@{bot_username}</a></b>"
                    try:
                        app.send_audio(chat_id=chat_id, audio=produced_mp3, thumb=audio_thumb, title=safe_base, caption=caption_html, parse_mode=enums.ParseMode.HTML)
                    except Exception:
                        app.send_audio(chat_id=chat_id, audio=produced_mp3, thumb=audio_thumb, title=safe_base, caption=caption_fallback, parse_mode=enums.ParseMode.HTML)
                    app.stop()
                    delete_pyrogram_session_files(session_base)
                except Exception as e2:
                    print(f"Failed to send audio (both Bot API and Pyrogram): {e} | {e2}")
                    # Inform user
                    bot_api_send_message(chat_id, f"Send failed: {e2}")
                    db.set_work(user_id_for_work or chat_id, 0)
                    try:
                        delete_file(output_path)
                    except Exception:
                        pass
                    return
        else:
            session_base = f"sessions/{(session_id if session_id is not None else chat_id)}"
            app = Client(session_base, bot_token=config.bot_token, api_id=config.api_id, api_hash=config.api_hash)
            app.start()
            try:
                caption_html = f"{tge('gem', '💎')} <b><a href='https://t.me/{bot_username}'>@{bot_username}</a></b>"
                caption_fallback = f"💎 <b><a href='https://t.me/{bot_username}'>@{bot_username}</a></b>"
                try:
                    app.send_audio(chat_id=chat_id, audio=produced_mp3, thumb=audio_thumb, title=safe_base, caption=caption_html, parse_mode=enums.ParseMode.HTML)
                except Exception:
                    app.send_audio(chat_id=chat_id, audio=produced_mp3, thumb=audio_thumb, title=safe_base, caption=caption_fallback, parse_mode=enums.ParseMode.HTML)
            except Exception as e:
                print(f"Failed to send audio: {e}")
                bot_api_send_message(chat_id, f"Send failed: {e}")
                app.stop()
                delete_pyrogram_session_files(session_base)
                db.set_work(user_id_for_work or chat_id, 0)
                try:
                    delete_file(output_path)
                except Exception:
                    pass
                return
            app.stop()
            delete_pyrogram_session_files(session_base)
        if audio_thumb and os.path.exists(audio_thumb) and (audio_thumb != produced_mp3):
            try:
                delete_file(audio_thumb)
            except Exception:
                pass
    except Exception as e:
        sent = bot_api_send_message(chat_id, f"Download error: {e}", payment_payload)
        if not sent:
            try:
                session_base = f"sessions/{(session_id if session_id is not None else chat_id)}"
                app = Client(session_base, bot_token=config.bot_token, api_id=config.api_id, api_hash=config.api_hash)
                app.start()
                app.send_message(chat_id=chat_id, text=f"Download error: {e}")
                app.stop()
                delete_pyrogram_session_files(session_base)
            except Exception:
                pass
    db.set_work(user_id_for_work or chat_id, 0)
    try:
        delete_file(output_path)
    except Exception:
        pass

def get_video_formats(url, domain):
    ck = select_cookiefile(url or domain)
    info = {}
    err = ''
    
    # Пробуем с cookies если есть
    if ck:
        try:
            args = ["--dump-single-json", "--no-warnings", "--no-progress", "--cookies", ck]
            args += _yt_dlp_runtime_args()
            if _is_youtube(url or domain):
                args += _youtube_extractor_args()
            args += [url]
            out, err = run_yt_dlp_process(args, capture_output=True, return_stderr=True)
            try:
                info = json.loads(out)
                if info and info.get('title'):
                    # Optional: extra `--list-formats` for debug logs
                    if getattr(config, 'yt_dlp_log_list_formats', False):
                        _run_list_formats_for_logs(url, domain, ck)
                    return info, err
            except Exception:
                info = {}
        except Exception as e:
            err = str(e)
            info = {}
    
    # Если с cookies не получилось, пробуем без них
    try:
        args = ["--dump-single-json", "--no-warnings", "--no-progress"]
        args += _yt_dlp_runtime_args()
        if _is_youtube(url or domain):
            args += _youtube_extractor_args()
        args += [url]
        out, err2 = run_yt_dlp_process(args, capture_output=True, return_stderr=True)
        try:
            info = json.loads(out)
            err = err + '\n' + err2 if err else err2
        except Exception:
            info = {}
    except Exception as e:
        err = err + '\n' + str(e) if err else str(e)
        info = {}
    
    if getattr(config, 'yt_dlp_log_list_formats', False):
        _run_list_formats_for_logs(url, domain, ck)
    
    return info, (err or '')


def _run_list_formats_for_logs(url, domain, ck=None):
    try:
        lf_args = ["--list-formats"]
        if ck:
            lf_args = ["--cookies", ck] + lf_args
        lf_args += _yt_dlp_runtime_args()
        if _is_youtube(url or domain):
            lf_args += _youtube_extractor_args()
        lf_args += [url]
        
        exe = getattr(config, 'yt_dlp_executable', None) or dlp_manager.get_selected_executable() or 'yt-dlp'
        cmd = [exe] + lf_args

        logger = _get_ytdlp_logger()
        logger.info("yt-dlp list-formats: %s", _cmd_to_str(_sanitize_cmd_for_logging(cmd)))
        
        # Stream combined stdout/stderr line-by-line to console
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors='replace')
        if proc.stdout is not None:
            for line in proc.stdout:
                try:
                    s = (line or '').rstrip()
                    if s:
                        logger.info("%s", s)
                    if getattr(config, 'show_yt_dlp_output', True):
                        print(s)
                except Exception:
                    pass
        proc.wait()
    except Exception:
        # ignore streaming errors
        pass

def is_youtube_public(url: str) -> bool:
    try:
        ck = select_cookiefile(url)
        info = get_info_json(url, ck)
    except Exception:
        return False
    fmts = info.get('formats', []) if isinstance(info, dict) else []
    for f in fmts:
        try:
            if f.get('vcodec') == 'images':
                continue

            proto = (f.get('protocol') or '').lower()
            if not proto:
                continue

            if proto in ('m3u8', 'm3u8_native', 'https', 'http', 'dash', 'http_dash_segments'):
                ext = (f.get('ext') or '').lower()
                if ext in ('mp4', 'webm', 'm4a'):
                    return True
        except Exception:
            continue
    return False

def get_domain(url):
    domain_pattern = r'^(https?:\/\/)?(www\.)?([a-zA-Z0-9.-]+\.[a-zA-Z]{2,})(\/.*)?$'
    match = re.match(domain_pattern, url)
    if match:
        return match.group(3)
    return None

def delete_file(f_path):
    for _ in range(5):
        try:
            os.remove(f_path)
            break
        except:
            pass
        time.sleep(5)


def ensure_jpg_thumb(thumb_path, video_path, out_base):
    if not out_base:
        return None
    target = os.path.join('downloads', f"{out_base}_thumb.jpg")
    try:
        if thumb_path and os.path.exists(thumb_path):
            try:
                with Image.open(thumb_path) as im:
                    rgb = im.convert('RGB')
                    max_w = 1280
                    if rgb.width > max_w:
                        h = int(max_w * rgb.height / rgb.width)
                        rgb = rgb.resize((max_w, h), Image.LANCZOS)
                    rgb.save(target, format='JPEG', quality=85)
                    return target
            except Exception:
                pass

        if video_path and os.path.exists(video_path):
            try:
                cmd = [
                    'ffmpeg', '-y', '-i', video_path,
                    '-ss', '00:00:01', '-vframes', '1',
                    '-q:v', '2', target
                ]
                subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if os.path.exists(target):
                    try:
                        with Image.open(target) as im2:
                            im2.convert('RGB')
                        return target
                    except Exception:
                        delete_file(target)
            except Exception:
                pass
    except Exception:
        pass
    return None


def simple_downloader_with_cancel(url, output_path, chat_id, domain, video_format=None, title_orig="", thumb=None,
                                 user_id_for_work=None, session_id=None, download_id: str = None, payment_payload=None):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    final_status = "completed"
    
    start_message_id = None
    if download_id:
        try:
            download_info = db.get_download_by_id(download_id)
            if download_info and download_info[8]:  # message_id is at index 8
                start_message_id = download_info[8]
        except Exception:
            pass
    
    try:
        ydl_opts = {
            'format': 'best',
            'outtmpl': output_path,
            'noprogress': True,
            'quiet': True
        }
        ck_all = select_cookiefile(url or domain)
        if ck_all:
            ydl_opts['cookiefile'] = ck_all
        if domain == "instagram.com":
            ydl_opts['quiet'] = True
        elif domain.startswith("youtu"):
            try:
                ck = select_cookiefile(url)
                try:
                    info = get_info_json(url, ck)
                except Exception:
                    info = {}
            except Exception:
                info = {}
            fmts = info.get('formats', []) if isinstance(info, dict) else []
            selected = None
            for f in fmts:
                if str(f.get('format_id')) == str(video_format):
                    selected = f
                    break

            # Если есть набор форматов с разными языками для одного и того же качества,
            # и среди них есть вариант с меткой (original), то для этого качества
            # всегда берём именно (original), независимо от того, на какой язык нажал пользователь.
            try:
                if isinstance(selected, dict) and fmts:
                    sel_height = selected.get('height')
                    sel_fps = selected.get('fps')
                    # Посчитаем, сколько языков есть для этого качества
                    same_quality = []
                    for fm in fmts:
                        if fm.get('vcodec') == 'images':
                            continue
                        if sel_height is not None and fm.get('height') != sel_height:
                            continue
                        if sel_fps is not None and fm.get('fps') != sel_fps:
                            continue
                        same_quality.append(fm)
                    if len(same_quality) > 1:
                        # Есть несколько языков -> ищем (original)
                        original_fmt = None
                        for fm in same_quality:
                            note = (fm.get('format_note') or '').lower()
                            # yt-dlp обычно пишет "[en-US] (original)" в конце MORE INFO
                            if '(original)' in note or '[en-us] (original)' in note:
                                original_fmt = fm
                                break
                        if original_fmt is not None:
                            selected = original_fmt
                            video_format = selected.get('format_id', video_format)
            except Exception:
                pass

            if selected and selected.get('acodec') and selected.get('acodec') != 'none':
                ydl_opts['format'] = str(video_format)
            else:
                ydl_opts['format'] = f"{video_format}+bestaudio/best"
                ydl_opts['merge_output_format'] = "mp4"
            
        # Build args for yt-dlp CLI
        args = []
        if ydl_opts.get('format'):
            args += ['-f', ydl_opts['format']]
        if ydl_opts.get('outtmpl'):
            args += ['-o', ydl_opts['outtmpl']]
        if ydl_opts.get('merge_output_format'):
            args += ['--merge-output-format', ydl_opts['merge_output_format']]
        if ck_all:
            args = ['--cookies', ck_all] + args
        args += _yt_dlp_runtime_args()
        if domain and domain.startswith('youtu'):
            args += _youtube_extractor_args()

        # Запускаем процесс с возможностью отмены
        proc = run_yt_dlp_process_with_pid(args + [url], download_id)
        
        # Читаем вывод процесса (и пишем в лог, чтобы при "Killed" остался контекст)
        output_lines = deque(maxlen=300)
        logger = _get_ytdlp_logger()
        log_path = getattr(proc, '_ytdlp_log_path', None)
        log_fh = None
        try:
            if log_path:
                log_fh = open(log_path, 'a', encoding='utf-8', errors='replace', buffering=1)
        except Exception:
            log_fh = None

        try:
            if proc.stdout:
                for raw in proc.stdout:
                    line = (raw or '').rstrip('\n')
                    if not line:
                        continue
                    if getattr(config, 'show_yt_dlp_output', True):
                        try:
                            print(line)
                        except Exception:
                            pass
                    output_lines.append(line)
                    try:
                        prefix = f"[{download_id}] " if download_id else ""
                        logger.info("%s%s", prefix, line)
                    except Exception:
                        pass
                    if log_fh is not None:
                        try:
                            log_fh.write(line + "\n")
                        except Exception:
                            pass
        finally:
            try:
                if log_fh is not None:
                    log_fh.close()
            except Exception:
                pass
        
        # Ждем завершения процесса
        proc.wait()
        
        # Проверяем статус загрузки в БД
        if download_id:
            download_info = db.get_download_by_id(download_id)
            if download_info and download_info[7] == 'cancelled':  # status field
                # Загрузка была отменена
                final_status = "cancelled"
                if start_message_id:
                    update_download_message(chat_id, start_message_id, f"{tge('no', '❌')} Загрузка отменена пользователем.")
                db.set_work(user_id_for_work or chat_id, 0)
                delete_file(output_path)
                return
        
        if proc.returncode != 0:
            final_status = "failed"
            error_msg = f"yt-dlp failed ({_rc_to_reason(proc.returncode)})"
            if output_lines:
                try:
                    error_msg += f"\nLast output: {list(output_lines)[-1]}"
                except Exception:
                    pass
            if log_path:
                error_msg += f"\nLog: {log_path}"
            
            if start_message_id:
                update_download_message(chat_id, start_message_id, f"{tge('no', '❌')} Ошибка загрузки: {html.escape(error_msg)}")
            bot_api_send_message(chat_id, f"Download failed: {error_msg}", payment_payload)
            
            db.set_work(user_id_for_work or chat_id, 0)
            delete_file(output_path)
            return

        # Обновляем сообщение о успешной загрузке
        if start_message_id:
            update_download_message(chat_id, start_message_id, f"{tge('check', '✅')} Загрузка завершена. Отправляю файл...")

        try:
            ck = select_cookiefile(url)
            try:
                info_dict = get_info_json(url, ck)
            except Exception:
                info_dict = {}
        except Exception:
            info_dict = {}

        width = 0
        height = 0
        fs = info_dict.get('formats', [])
        for f in fs:
            if f.get('format_id') == video_format:
                width = f.get('width', 0) or 0
                height = f.get('height', 0) or 0

        base_name = os.path.splitext(os.path.basename(output_path))[0]
        safe_base = sanitize_filename(base_name)
        norm_thumb = ensure_jpg_thumb(thumb, output_path, safe_base)

        # Try Bot API upload for files <= 50MB, else fallback to Pyrogram
        try:
            size_bytes = os.path.getsize(output_path)
        except Exception:
            size_bytes = 0

        if size_bytes and size_bytes <= 50 * 1024 * 1024:
            try:
                api_url = f"https://api.telegram.org/bot{config.bot_token}/sendVideo"
                data = {
                    'chat_id': str(chat_id),
                    'caption': title_orig or safe_base,
                    'supports_streaming': 'true',
                }
                with open(output_path, 'rb') as vf:
                    files = {
                        'video': (os.path.basename(output_path), vf)
                    }
                    if norm_thumb and os.path.exists(norm_thumb):
                        with open(norm_thumb, 'rb') as tf:
                            files['thumbnail'] = (os.path.basename(norm_thumb), tf)
                            resp = requests.post(api_url, data=data, files=files, timeout=120)
                    else:
                        resp = requests.post(api_url, data=data, files=files, timeout=120)
                if resp.status_code != 200 or not resp.json().get('ok', False):
                    raise RuntimeError(f"Bot API sendVideo failed: HTTP {resp.status_code} {resp.text[:200]}")
            except Exception as e:
                # Fallback to Pyrogram if Bot API fails
                try:
                    session_base = f"sessions/{(session_id if session_id is not None else chat_id)}"
                    app = Client(session_base, bot_token=config.bot_token, api_id=config.api_id, api_hash=config.api_hash)
                    app.start()
                    if norm_thumb and os.path.exists(norm_thumb):
                        app.send_video(chat_id=chat_id, video=output_path, caption=title_orig, thumb=norm_thumb, width=width, height=height)
                    else:
                        app.send_video(chat_id=chat_id, video=output_path, caption=title_orig, thumb=thumb, width=width, height=height)
                    app.stop()
                    delete_pyrogram_session_files(session_base)
                except Exception as e2:
                    print(f"Failed to send video (both Bot API and Pyrogram): {e} | {e2}")
                    final_status = "failed"
                    bot_api_send_message(chat_id, f"Send failed: {e2}", payment_payload)
        else:
            session_base = f"sessions/{(session_id if session_id is not None else chat_id)}"
            app = Client(session_base, bot_token=config.bot_token, api_id=config.api_id, api_hash=config.api_hash)
            app.start()
            try:
                if norm_thumb and os.path.exists(norm_thumb):
                    app.send_video(chat_id=chat_id, video=output_path, caption=title_orig, thumb=norm_thumb, width=width, height=height)
                else:
                    app.send_video(chat_id=chat_id, video=output_path, caption=title_orig, thumb=thumb, width=width, height=height)
            except Exception as e:
                print(f"Failed to send video: {e}")
                final_status = "failed"
                bot_api_send_message(chat_id, f"Send failed: {e}", payment_payload)
            app.stop()
            delete_pyrogram_session_files(session_base)
            
    except Exception as e:
        final_status = "failed"
        try:
            if domain in ["instagram.com", "twitter.com", "x.com"]:
                output_path = output_path.replace("mp4", "jpg")
                command = [
                    "gallery-dl",
                    "--config", "gallery-dl.conf",
                    "--filename", output_path.replace("downloads/", ""),
                    "--directory", "downloads/",
                    url
                ]
                subprocess.run(command)
                # Try Bot API sendPhoto first
                try:
                    api = f"https://api.telegram.org/bot{config.bot_token}/sendPhoto"
                    with open(output_path, 'rb') as pf:
                        files = {'photo': (os.path.basename(output_path), pf)}
                        data = {'chat_id': str(chat_id)}
                        r = requests.post(api, data=data, files=files, timeout=120)
                        ok = (r.status_code == 200 and r.json().get('ok'))
                    if not ok:
                        raise RuntimeError(r.text[:200])
                except Exception:
                    session_base = f"sessions/{(session_id if session_id is not None else chat_id)}"
                    app = Client(session_base, bot_token=config.bot_token, api_id=config.api_id, api_hash=config.api_hash)
                    app.start()
                    app.send_photo(chat_id=chat_id, photo=output_path)
                    app.stop()
                    delete_pyrogram_session_files(session_base)
        except Exception:
            # Prefer Bot API error notification
            bot_api_send_message(chat_id, f"Download error: {e}", payment_payload)
    finally:
        # Обновляем статус в БД
        if download_id:
            db.update_download_status(download_id, final_status)
            db.remove_active_download(download_id)
        
        # Удаляем сообщение "Starting download..."
        if start_message_id:
            delete_download_message(chat_id, start_message_id)
        
        db.set_work(user_id_for_work or chat_id, 0)
        delete_file(output_path)


def simple_downloader(url, output_path, chat_id, domain, video_format=None, title_orig="", thumb=None, payment_payload=None, user_id_for_work=None, session_id=None):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        ydl_opts = {
            'format': 'best',
            'outtmpl': output_path,
            'noprogress': True,
            'quiet': True
        }
        ck_all = select_cookiefile(url or domain)
        if ck_all:
            ydl_opts['cookiefile'] = ck_all
        if domain == "instagram.com":
            ydl_opts['quiet'] = True
        elif domain.startswith("youtu"):
            try:
                ck = select_cookiefile(url)
                try:
                    info = get_info_json(url, ck)
                except Exception:
                    info = {}
            except Exception:
                info = {}
            fmts = info.get('formats', []) if isinstance(info, dict) else []
            selected = None
            for f in fmts:
                if str(f.get('format_id')) == str(video_format):
                    selected = f
                    break

            # Если есть набор форматов с разными языками для одного и того же качества,
            # и среди них есть вариант с меткой (original), то для этого качества
            # всегда берём именно (original), независимо от того, на какой язык нажал пользователь.
            try:
                if isinstance(selected, dict) and fmts:
                    sel_height = selected.get('height')
                    sel_fps = selected.get('fps')
                    # Посчитаем, сколько языков есть для этого качества
                    same_quality = []
                    for fm in fmts:
                        if fm.get('vcodec') == 'images':
                            continue
                        if sel_height is not None and fm.get('height') != sel_height:
                            continue
                        if sel_fps is not None and fm.get('fps') != sel_fps:
                            continue
                        same_quality.append(fm)
                    if len(same_quality) > 1:
                        # Есть несколько языков -> ищем (original)
                        original_fmt = None
                        for fm in same_quality:
                            note = (fm.get('format_note') or '').lower()
                            # yt-dlp обычно пишет "[en-US] (original)" в конце MORE INFO
                            if '(original)' in note or '[en-us] (original)' in note:
                                original_fmt = fm
                                break
                        if original_fmt is not None:
                            selected = original_fmt
                            video_format = selected.get('format_id', video_format)
            except Exception:
                pass

            if selected and selected.get('acodec') and selected.get('acodec') != 'none':
                ydl_opts['format'] = str(video_format)
            else:
                ydl_opts['format'] = f"{video_format}+bestaudio/best"
                ydl_opts['merge_output_format'] = "mp4"
            # NOTE: extractor args must be passed as CLI flags; see _youtube_extractor_args()
        # Build args for yt-dlp CLI
        args = []
        if ydl_opts.get('format'):
            args += ['-f', ydl_opts['format']]
        if ydl_opts.get('outtmpl'):
            args += ['-o', ydl_opts['outtmpl']]
        # Always show progress/logs for yt-dlp (do not add quiet/no-progress)
        if ydl_opts.get('merge_output_format'):
            args += ['--merge-output-format', ydl_opts['merge_output_format']]
        if ck_all:
            args = ['--cookies', ck_all] + args
        args += _yt_dlp_runtime_args()
        if domain and domain.startswith('youtu'):
            args += _youtube_extractor_args()

        max_retries = 3
        last_exc = None
        for attempt in range(1, max_retries + 1):
            try:
                run_yt_dlp_process(args + [url])
                last_exc = None
                break
            except Exception as de:
                last_exc = de
                time.sleep(min(2 * attempt, 6))
                continue

        if last_exc is not None:
            msg = str(last_exc)
            sent = bot_api_send_message(chat_id, f"Download failed after {max_retries} attempts: {msg}", payment_payload)
            if not sent:
                try:
                    session_base = f"sessions/{(session_id if session_id is not None else chat_id)}"
                    app = Client(session_base, bot_token=config.bot_token, api_id=config.api_id, api_hash=config.api_hash)
                    app.start()
                    app.send_message(chat_id=chat_id, text=f"Download failed after {max_retries} attempts: {msg}")
                    app.stop()
                    delete_pyrogram_session_files(session_base)
                except Exception:
                    pass
            db.set_work(user_id_for_work or chat_id, 0)
            delete_file(output_path)
            return

        try:
            ck = select_cookiefile(url)
            try:
                info_dict = get_info_json(url, ck)
            except Exception:
                info_dict = {}
        except Exception:
            info_dict = {}

        width = 0
        height = 0
        fs = info_dict.get('formats', [])
        for f in fs:
            if f.get('format_id') == video_format:
                width = f.get('width', 0) or 0
                height = f.get('height', 0) or 0

        base_name = os.path.splitext(os.path.basename(output_path))[0]
        safe_base = sanitize_filename(base_name)
        norm_thumb = ensure_jpg_thumb(thumb, output_path, safe_base)

        # Try Bot API upload for files <= 50MB, else fallback to Pyrogram
        try:
            size_bytes = os.path.getsize(output_path)
        except Exception:
            size_bytes = 0

        if size_bytes and size_bytes <= 50 * 1024 * 1024:
            try:
                url = f"https://api.telegram.org/bot{config.bot_token}/sendVideo"
                data = {
                    'chat_id': str(chat_id),
                    'caption': title_orig or safe_base,
                    'supports_streaming': 'true',
                }
                with open(output_path, 'rb') as vf:
                    files = {
                        'video': (os.path.basename(output_path), vf)
                    }
                    if norm_thumb and os.path.exists(norm_thumb):
                        with open(norm_thumb, 'rb') as tf:
                            files['thumbnail'] = (os.path.basename(norm_thumb), tf)
                            resp = requests.post(url, data=data, files=files, timeout=120)
                    else:
                        resp = requests.post(url, data=data, files=files, timeout=120)
                if resp.status_code != 200 or not resp.json().get('ok', False):
                    raise RuntimeError(f"Bot API sendVideo failed: HTTP {resp.status_code} {resp.text[:200]}")
            except Exception as e:
                # Fallback to Pyrogram if Bot API fails
                try:
                    session_base = f"sessions/{(session_id if session_id is not None else chat_id)}"
                    app = Client(session_base, bot_token=config.bot_token, api_id=config.api_id, api_hash=config.api_hash)
                    app.start()
                    if norm_thumb and os.path.exists(norm_thumb):
                        app.send_video(chat_id=chat_id, video=output_path, caption=title_orig, thumb=norm_thumb, width=width, height=height)
                    else:
                        app.send_video(chat_id=chat_id, video=output_path, caption=title_orig, thumb=thumb, width=width, height=height)
                    app.stop()
                    delete_pyrogram_session_files(session_base)
                except Exception as e2:
                    print(f"Failed to send video (both Bot API and Pyrogram): {e} | {e2}")
                    bot_api_send_message(chat_id, f"Send failed: {e2}", payment_payload)
                    db.set_work(user_id_for_work or chat_id, 0)
                    try:
                        delete_file(output_path)
                    except Exception:
                        pass
                    return
        else:
            session_base = f"sessions/{(session_id if session_id is not None else chat_id)}"
            app = Client(session_base, bot_token=config.bot_token, api_id=config.api_id, api_hash=config.api_hash)
            app.start()
            try:
                if norm_thumb and os.path.exists(norm_thumb):
                    app.send_video(chat_id=chat_id, video=output_path, caption=title_orig, thumb=norm_thumb, width=width, height=height)
                else:
                    app.send_video(chat_id=chat_id, video=output_path, caption=title_orig, thumb=thumb, width=width, height=height)
            except Exception as e:
                print(f"Failed to send video: {e}")
                bot_api_send_message(chat_id, f"Send failed: {e}", payment_payload)
                app.stop()
                delete_pyrogram_session_files(session_base)
                db.set_work(user_id_for_work or chat_id, 0)
                try:
                    delete_file(output_path)
                except Exception:
                    pass
                return
            app.stop()
            delete_pyrogram_session_files(session_base)
    except Exception as e:
        try:
            if domain in ["instagram.com", "twitter.com", "x.com"]:
                output_path = output_path.replace("mp4", "jpg")
                command = [
                    "gallery-dl",
                    "--config", "gallery-dl.conf",
                    "--filename", output_path.replace("downloads/", ""),
                    "--directory", "downloads/",
                    url
                ]
                subprocess.run(command)
                # Try Bot API sendPhoto first
                try:
                    api = f"https://api.telegram.org/bot{config.bot_token}/sendPhoto"
                    with open(output_path, 'rb') as pf:
                        files = {'photo': (os.path.basename(output_path), pf)}
                        data = {'chat_id': str(chat_id)}
                        r = requests.post(api, data=data, files=files, timeout=120)
                        ok = (r.status_code == 200 and r.json().get('ok'))
                    if not ok:
                        raise RuntimeError(r.text[:200])
                except Exception:
                    session_base = f"sessions/{(session_id if session_id is not None else chat_id)}"
                    app = Client(session_base, bot_token=config.bot_token, api_id=config.api_id, api_hash=config.api_hash)
                    app.start()
                    app.send_photo(chat_id=chat_id, photo=output_path)
                    app.stop()
                    delete_pyrogram_session_files(session_base)
        except Exception:
            # Prefer Bot API error notification
            bot_api_send_message(chat_id, f"Download error: {e}", payment_payload)
    db.set_work(user_id_for_work or chat_id, 0)
    delete_file(output_path)


def download_audio_with_cancel(video_url, output_path, chat_id, thumb, bot_username,
                              user_id_for_work=None, session_id=None, download_id: str = None, payment_payload=None):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    final_status = "completed"
    
    start_message_id = None
    if download_id:
        try:
            download_info = db.get_download_by_id(download_id)
            if download_info and download_info[8]:  # message_id is at index 8
                start_message_id = download_info[8]
        except Exception:
            pass
    
    try:
        if not output_path.lower().endswith('.mp3'):
            output_path = output_path + '.mp3'

        base_name = os.path.basename(output_path)
        base_no_ext = os.path.splitext(base_name)[0]
        safe_base = sanitize_filename(base_no_ext)
        outtmpl = os.path.join('downloads', safe_base)

        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': outtmpl,
            'writethumbnail': True,
            'noprogress': True,
            'quiet': True,
        }

        ck = select_cookiefile(video_url)

        # Build base args
        args = []
        if ydl_opts.get('format'):
            args += ['-f', ydl_opts['format']]
        if ydl_opts.get('outtmpl'):
            args += ['-o', ydl_opts['outtmpl']]
        if ydl_opts.get('writethumbnail'):
            args += ['--write-thumbnail']
        # extract audio to mp3, embed thumbnail, add metadata
        args += ['--extract-audio', '--audio-format', 'mp3', '--audio-quality', '128', '--embed-thumbnail', '--add-metadata']
        if ck:
            args = ['--cookies', ck] + args
        args += _yt_dlp_runtime_args()
        if _is_youtube(video_url):
            args += _youtube_extractor_args()

        # Запускаем процесс с возможностью отмены
        proc = run_yt_dlp_process_with_pid(args + [video_url], download_id)
        
        # Читаем вывод процесса
        output_lines = []
        if proc.stdout:
            for line in proc.stdout:
                line = line.strip()
                if line:
                    print(line)
                    output_lines.append(line)
        
        # Ждем завершения процесса
        proc.wait()
        
        # Проверяем статус загрузки в БД
        if download_id:
            download_info = db.get_download_by_id(download_id)
            if download_info and download_info[7] == 'cancelled':  # status field
                # Загрузка была отменена
                final_status = "cancelled"
                if start_message_id:
                    update_download_message(chat_id, start_message_id, f"{tge('no', '❌')} Загрузка отменена пользователем.")
                db.set_work(user_id_for_work or chat_id, 0)
                delete_file(output_path)
                return
        
        if proc.returncode != 0:
            final_status = "failed"
            error_msg = f"yt-dlp exited with code {proc.returncode}"
            if output_lines:
                error_msg += f"\nLast output: {output_lines[-1] if output_lines else 'No output'}"
            
            if start_message_id:
                update_download_message(chat_id, start_message_id, f"{tge('no', '❌')} Ошибка загрузки: {html.escape(error_msg)}")
            bot_api_send_message(chat_id, f"Download failed: {error_msg}", payment_payload)
            
            db.set_work(user_id_for_work or chat_id, 0)
            delete_file(output_path)
            return

        # Обновляем сообщение о успешной загрузке
        if start_message_id:
            update_download_message(chat_id, start_message_id, f"{tge('check', '✅')} Загрузка завершена. Отправляю файл...")

        possible_thumb = None
        for ext in ('.jpg', '.jpeg', '.webp', '.png'):
            candidate = outtmpl + ext
            if os.path.exists(candidate):
                possible_thumb = candidate
                break

        audio_thumb = None
        if possible_thumb:
            audio_thumb = outtmpl + '_audio.jpg'
            try:
                crop_to_square(possible_thumb, audio_thumb)
            except Exception:
                audio_thumb = possible_thumb
        else:
            if thumb and os.path.exists(thumb):
                audio_thumb = outtmpl + '_audio.jpg'
                try:
                    crop_to_square(thumb, audio_thumb)
                except Exception:
                    audio_thumb = thumb

        produced_mp3 = outtmpl + '.mp3'
        # Try Bot API upload for files <= 50MB, else fallback to Pyrogram
        try:
            size_bytes = os.path.getsize(produced_mp3)
        except Exception:
            size_bytes = 0

        if size_bytes and size_bytes <= 50 * 1024 * 1024:
            try:
                url = f"https://api.telegram.org/bot{config.bot_token}/sendAudio"
                data = {
                    'chat_id': str(chat_id),
                    'caption': f"{tge('gem', '💎')} <b><a href='https://t.me/{bot_username}'>@{bot_username}</a></b>",
                    'parse_mode': 'HTML',
                    'title': safe_base,
                }
                with open(produced_mp3, 'rb') as af:
                    files = {
                        'audio': (os.path.basename(produced_mp3), af)
                    }
                    if audio_thumb and os.path.exists(audio_thumb):
                        with open(audio_thumb, 'rb') as tf:
                            files['thumbnail'] = (os.path.basename(audio_thumb), tf)
                            resp = requests.post(url, data=data, files=files, timeout=120)
                    else:
                        resp = requests.post(url, data=data, files=files, timeout=120)
                if resp.status_code != 200 or not resp.json().get('ok', False):
                    raise RuntimeError(f"Bot API sendAudio failed: HTTP {resp.status_code} {resp.text[:200]}")
            except Exception as e:
                # Fallback to Pyrogram if Bot API fails
                try:
                    session_base = f"sessions/{(session_id if session_id is not None else chat_id)}"
                    app = Client(session_base, bot_token=config.bot_token, api_id=config.api_id, api_hash=config.api_hash)
                    app.start()
                    caption_html = f"{tge('gem', '💎')} <b><a href='https://t.me/{bot_username}'>@{bot_username}</a></b>"
                    caption_fallback = f"💎 <b><a href='https://t.me/{bot_username}'>@{bot_username}</a></b>"
                    try:
                        app.send_audio(chat_id=chat_id, audio=produced_mp3, thumb=audio_thumb, title=safe_base, caption=caption_html, parse_mode=enums.ParseMode.HTML)
                    except Exception:
                        app.send_audio(chat_id=chat_id, audio=produced_mp3, thumb=audio_thumb, title=safe_base, caption=caption_fallback, parse_mode=enums.ParseMode.HTML)
                    app.stop()
                    delete_pyrogram_session_files(session_base)
                except Exception as e2:
                    print(f"Failed to send audio (both Bot API and Pyrogram): {e} | {e2}")
                    final_status = "failed"
                    bot_api_send_message(chat_id, f"Send failed: {e2}", payment_payload)
        else:
            session_base = f"sessions/{(session_id if session_id is not None else chat_id)}"
            app = Client(session_base, bot_token=config.bot_token, api_id=config.api_id, api_hash=config.api_hash)
            app.start()
            try:
                caption_html = f"{tge('gem', '💎')} <b><a href='https://t.me/{bot_username}'>@{bot_username}</a></b>"
                caption_fallback = f"💎 <b><a href='https://t.me/{bot_username}'>@{bot_username}</a></b>"
                try:
                    app.send_audio(chat_id=chat_id, audio=produced_mp3, thumb=audio_thumb, title=safe_base, caption=caption_html, parse_mode=enums.ParseMode.HTML)
                except Exception:
                    app.send_audio(chat_id=chat_id, audio=produced_mp3, thumb=audio_thumb, title=safe_base, caption=caption_fallback, parse_mode=enums.ParseMode.HTML)
            except Exception as e:
                print(f"Failed to send audio: {e}")
                final_status = "failed"
                bot_api_send_message(chat_id, f"Send failed: {e}", payment_payload)
            app.stop()
            delete_pyrogram_session_files(session_base)
        
        if audio_thumb and os.path.exists(audio_thumb) and (audio_thumb != produced_mp3):
            try:
                delete_file(audio_thumb)
            except Exception:
                pass
                
    except Exception as e:
        final_status = "failed"
        sent = bot_api_send_message(chat_id, f"Download error: {e}", payment_payload)
        if not sent:
            try:
                session_base = f"sessions/{(session_id if session_id is not None else chat_id)}"
                app = Client(session_base, bot_token=config.bot_token, api_id=config.api_id, api_hash=config.api_hash)
                app.start()
                app.send_message(chat_id=chat_id, text=f"Download error: {e}")
                app.stop()
                delete_pyrogram_session_files(session_base)
            except Exception:
                pass
    finally:
        # Обновляем статус в БД
        if download_id:
            db.update_download_status(download_id, final_status)
            db.remove_active_download(download_id)
        
        # Удаляем сообщение "Starting download..."
        if start_message_id:
            delete_download_message(chat_id, start_message_id)
        
        db.set_work(user_id_for_work or chat_id, 0)
        try:
            delete_file(output_path)
        except Exception:
            pass


def delete_pyrogram_session_files(session_base: str):
    return
