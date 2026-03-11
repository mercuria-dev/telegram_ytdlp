import os
import requests
import json
import time
import shutil
import stat
import platform as _platform
import config

# dlp directory at repository root: ../dlp relative to modules/
DLP_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', 'dlp'))
INSTALLED_JSON = os.path.join(DLP_DIR, 'installed.json')
GITHUB_RELEASES = 'https://api.github.com/repos/yt-dlp/yt-dlp/releases'


def _pick_windows_asset(assets: list[dict]) -> dict | None:
    """Pick best Windows 64-bit yt-dlp asset from a GitHub release assets list.

    GitHub releases may include multiple .exe builds (x64, x86, arm64).
    We strongly prefer x64/amd64 and avoid x86/arm64 by filename heuristics.
    """
    best = None
    best_score = None
    for a in assets or []:
        name = (a.get('name') or '')
        nl = name.lower()
        if not nl.endswith('.exe'):
            continue
        if 'yt-dlp' not in nl:
            continue

        score = 0
        # Exact canonical name from upstream is typically the 64-bit Windows build.
        if nl == 'yt-dlp.exe':
            score += 1000

        # Prefer explicit x64 markers
        if ('x64' in nl) or ('x86_64' in nl) or ('amd64' in nl):
            score += 200

        # De-prioritize / avoid other architectures
        if ('arm64' in nl) or ('aarch64' in nl):
            score -= 200
        # x86 / 32-bit markers
        if ('x86' in nl) or ('win32' in nl) or ('i386' in nl) or ('32' in nl and 'x86_64' not in nl):
            score -= 150

        # Small penalty for "min" builds (still usable, but not preferred)
        if 'min' in nl:
            score -= 5

        if best_score is None or score > best_score:
            best_score = score
            best = a

    return best


def ensure_dlp_dir():
    if not os.path.exists(DLP_DIR):
        os.makedirs(DLP_DIR, exist_ok=True)


def _load_installed():
    try:
        if os.path.exists(INSTALLED_JSON):
            with open(INSTALLED_JSON, 'rt', encoding='utf-8') as f:
                return json.load(f)
    except Exception:
        pass
    return {'installed': [], 'selected': None}


def _save_installed(data):
    try:
        with open(INSTALLED_JSON, 'wt', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


def _make_executable(path):
    try:
        st = os.stat(path)
        os.chmod(path, st.st_mode | stat.S_IEXEC)
    except Exception:
        pass


def _download_file(url, target_path):
    tmp = target_path + ".tmp"
    try:
        with requests.get(url, stream=True, timeout=30) as r:
            r.raise_for_status()
            with open(tmp, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
        shutil.move(tmp, target_path)
        _make_executable(target_path)
        return True
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        return False


def download_latest_releases(count: int = 2, prefer_asset_names=None):
    """Download latest `count` releases' yt-dlp executables into `dlp/`.

    For each release scanned, attempt to download both Windows and Linux assets (if present).
    Installed records will include a 'platform' field with values 'windows' or 'linux'.
    The installed.json `selected` field will be a mapping of platform->path when possible.
    """
    ensure_dlp_dir()
    if prefer_asset_names is None:
        prefer_asset_names = ['yt-dlp.exe', 'yt-dlp']

    try:
        resp = requests.get(GITHUB_RELEASES + '?per_page=10', timeout=15)
        resp.raise_for_status()
        releases = resp.json()
    except Exception as e:
        return {'ok': False, 'error': f'GitHub API error: {e}'}

    installed = _load_installed()
    # installed may contain records with 'platform'
    installed_keys = {(i.get('tag'), i.get('platform')) for i in installed.get('installed', [])}

    downloaded = []
    # track found platforms
    found_platforms = set()

    for rel in releases:
        if len(downloaded) >= count * 2:
            break
        tag = rel.get('tag_name') or rel.get('name') or ''
        if not tag:
            continue
        assets = rel.get('assets', []) or []

        # find candidate for windows (prefer 64-bit)
        win_pick = _pick_windows_asset(assets)

        # find candidate for linux
        ln_pick = None
        for a in assets:
            name = a.get('name') or ''
            nl = name.lower()
            # prefer name exactly 'yt-dlp' (no extension) or with 'linux' in name
            if nl == 'yt-dlp' or ('yt-dlp' in nl and ('linux' in nl or nl.endswith('.xz') or nl.endswith('.gz') or nl.endswith('.bin') or nl.endswith('.zip') or '.' not in nl)):
                ln_pick = a
                break

        # fallback heuristics (should rarely trigger now)
        if not win_pick:
            for a in assets:
                name = (a.get('name') or '').lower()
                if name == 'yt-dlp.exe':
                    win_pick = a
                    break
        if not ln_pick:
            for a in assets:
                name = (a.get('name') or '').lower()
                if 'yt-dlp' in name and not name.endswith('.exe'):
                    ln_pick = a
                    break

        for platform_name, pick in (('windows', win_pick), ('linux', ln_pick)):
            if not pick:
                continue
            key = (tag, platform_name)
            if key in installed_keys:
                # already installed
                for rec in installed.get('installed', []):
                    if rec.get('tag') == tag and rec.get('platform') == platform_name:
                        downloaded.append(rec)
                        found_platforms.add(platform_name)
                        break
                continue

            url = pick.get('browser_download_url')
            if not url:
                continue
            ext = os.path.splitext(pick.get('name') or '')[1]
            fname = f"yt-dlp-{tag}-{platform_name}{ext or ''}"
            target = os.path.join(DLP_DIR, fname)
            ok = _download_file(url, target)
            if ok:
                rec = {'tag': tag, 'path': os.path.abspath(target), 'asset': pick.get('name'), 'downloaded_at': int(time.time()), 'platform': platform_name}
                installed.get('installed', []).append(rec)
                downloaded.append(rec)
                found_platforms.add(platform_name)

        # if we've got both windows and linux, we can stop early
        if 'windows' in found_platforms and 'linux' in found_platforms and len(downloaded) >= 2:
            break

    # Normalize installed: keep most recent entries, deduplicate by (platform, tag)
    try:
        all_inst = installed.get('installed', [])
        seen = set()
        uniq = []
        for r in sorted(all_inst, key=lambda x: x.get('downloaded_at', 0), reverse=True):
            key = (r.get('tag'), r.get('platform'))
            if key in seen:
                continue
            seen.add(key)
            uniq.append(r)
        # keep recent per platform up to `count` releases
        installed['installed'] = uniq[: max(count * 2, len(uniq))]

        # build selected mapping by platform
        sel_map = {}
        for p in ('windows', 'linux'):
            for r in uniq:
                if r.get('platform') == p:
                    sel_map[p] = r.get('path')
                    break
        installed['selected'] = sel_map
        _save_installed(installed)
    except Exception:
        pass

    return {'ok': True, 'downloaded': downloaded, 'installed': installed}


def get_selected_executable():
    ensure_dlp_dir()
    installed = _load_installed()
    preferred = getattr(config, 'yt_dlp_platform', 'auto') or 'auto'

    def _looks_like_non_x64_windows_exe(path: str | None) -> bool:
        try:
            if not path:
                return False
            name = os.path.basename(path).lower()
            # If explicitly x86_64/amd64/x64 -> treat as x64
            if ('x86_64' in name) or ('amd64' in name) or ('x64' in name):
                return False
            # Explicit non-x64 markers
            if ('arm64' in name) or ('aarch64' in name):
                return True
            # Beware: 'x86' also appears inside 'x86_64' which we handled above.
            if ('x86' in name) or ('win32' in name) or ('i386' in name):
                return True
            return False
        except Exception:
            return False
    # if selected is a mapping (platform->path)
    sel = installed.get('selected')
    if isinstance(sel, dict):
        # determine platform to use
        target_platform = preferred
        if preferred == 'auto':
            sys_pl = _platform.system().lower()
            target_platform = 'windows' if 'windows' in sys_pl else 'linux'
        path = sel.get(target_platform)
        if path and os.path.exists(path):
            if target_platform == 'windows' and _looks_like_non_x64_windows_exe(path):
                # Skip known non-x64 builds if present
                path = None
            else:
                return path
    # If selected is a plain path (backwards compat), return it
    if isinstance(sel, str) and sel and os.path.exists(sel):
        # Backwards compat: if this is Windows and looks non-x64, keep searching
        if preferred in ('windows', 'auto'):
            sys_pl = _platform.system().lower()
            if ('windows' in sys_pl) and _looks_like_non_x64_windows_exe(sel):
                pass
            else:
                return sel
        else:
            return sel

    # Try to find installed according to preferred platform
    ins = installed.get('installed', [])
    target_platform = preferred
    if preferred == 'auto':
        sys_pl = _platform.system().lower()
        target_platform = 'windows' if 'windows' in sys_pl else 'linux'
    for r in ins:
        if r.get('platform') == target_platform and os.path.exists(r.get('path')):
            pth = r.get('path')
            if target_platform == 'windows' and _looks_like_non_x64_windows_exe(pth):
                continue
            return pth

    # fallback: any installed executable
    for r in ins:
        p = r.get('path')
        if p and os.path.exists(p):
            if target_platform == 'windows' and _looks_like_non_x64_windows_exe(p):
                continue
            return p

    # fallback to system yt-dlp in PATH
    return 'yt-dlp'


if __name__ == '__main__':
    print('Downloading latest yt-dlp releases...')
    r = download_latest_releases(2)
    print(r)
