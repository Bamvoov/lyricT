#!/usr/bin/env python3
import subprocess
import time
import re
import os
import sys
import threading
import shutil
import colorsys
import warnings
import tempfile
from urllib.parse import unquote

warnings.filterwarnings("ignore", category=DeprecationWarning)

# GLOBALS
lyrics_timeline = []
plain_lyrics    = ""
artist_g        = ""
title_g         = ""
duration_g      = 0.0
fetch_done      = True
current_track   = ""
lyrics_cache    = {}
theme_color_g   = (45, 15, 22)
spectrum_g      = []
cava_running_g  = False

CACHE_DIR = os.path.expanduser("~/.cache/lyricT")
CACHE_FILE = os.path.join(CACHE_DIR, "lyrics_cache.json")
cache_lock = threading.Lock()
http_session = None

def get_http_session():
    global http_session
    if http_session is None:
        import requests
        http_session = requests.Session()
    return http_session

def load_cache():
    global lyrics_cache
    with cache_lock:
        try:
            if os.path.exists(CACHE_FILE):
                import json
                with open(CACHE_FILE, "r", encoding="utf-8") as f:
                    lyrics_cache = json.load(f)
        except Exception:
            pass

def save_cache():
    global lyrics_cache
    with cache_lock:
        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
            import json
            with open(CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(lyrics_cache, f)
        except Exception:
            pass

#  1. PLAYER FUNCTIONS

def get_active_player():
    try:
        out = subprocess.check_output(
            ["playerctl", "--list-all"], stderr=subprocess.DEVNULL
        ).decode().strip()
        players = [p for p in out.split('\n') if p.strip()]
        for player in players:
            status = subprocess.check_output(
                ["playerctl", "-p", player, "status"], stderr=subprocess.DEVNULL
            ).decode().strip()
            if status == "Playing":
                return player
    except Exception:
        pass
    return None

def get_player_data(player_name):
    """Single subprocess call for all metadata + position + length + artUrl."""
    try:
        fmt = "{{xesam:artist}}|||{{xesam:title}}|||{{position}}|||{{mpris:length}}|||{{mpris:artUrl}}"
        out = subprocess.check_output(
            ["playerctl", "-p", player_name, "metadata", "--format", fmt],
            stderr=subprocess.DEVNULL
        ).decode().strip()
        parts = out.split("|||")
        if len(parts) != 5:
            return None, None, 0.0, 0.0, ""
        artist, title, pos, length, art_url = parts
        position = float(pos) / 1_000_000 if pos.strip() else 0.0
        duration = float(length) / 1_000_000 if length.strip() else 0.0
        artist, title = clean_metadata(artist.strip(), title.strip())
        return artist, title, position, duration, art_url.strip()
    except Exception:
        return None, None, 0.0, 0.0, ""

def clean_metadata(artist, title):
    browsers = ["firefox", "chromium", "chrome", "brave", "opera"]
    if not artist or any(b in artist.lower() for b in browsers):
        if " - " in title:
            artist, title = title.split(" - ", 1)
        else:
            artist = "Unknown Artist"
    # Strip "(Official Video)", "[Lyrics]", etc.
    title = re.sub(
        r'\((Official|Music|Lyric|Audio|HD|HQ|4K).*?\)|\[.*?\]',
        '', title, flags=re.IGNORECASE
    ).strip()
    return artist.strip(), title.strip()

# ─── 2. LYRICS FETCH (runs in background thread) ─────────────

def fetch_lyrics_threaded(artist, title, track_id):
    global lyrics_timeline, plain_lyrics, duration_g, fetch_done, current_track, lyrics_cache

    timeline = []
    plain = ""
    api_duration = 0.0

    headers = {
        "User-Agent": "lyricT/1.0.0 (https://github.com/Bamvoov/lyricT)"
    }

    
    try:
        session = get_http_session()
        r = session.get(
            "https://lrclib.net/api/get",
            params={"artist_name": artist, "track_name": title},
            headers=headers,
            timeout=10
        )
        if r.status_code == 200:
            data = r.json()
            synced = data.get("syncedLyrics", "")
            plain_val = data.get("plainLyrics", "")
            api_duration = float(data.get("duration", 0.0))
            if synced:
                timeline = parse_lrc(synced)
                plain = plain_val
            elif plain_val:
                plain = plain_val
        elif r.status_code == 404:
            
            try:
                session = get_http_session()
                r = session.get(
                    "https://lrclib.net/api/search",
                    params={"q": f"{artist} {title}"},
                    headers=headers,
                    timeout=10
                )
                if r.status_code == 200:
                    results = r.json()
                    for item in results:
                        synced = item.get("syncedLyrics", "")
                        plain_val = item.get("plainLyrics", "")
                        dur_val = float(item.get("duration", 0.0))
                        if synced:
                            timeline = parse_lrc(synced)
                            plain = plain_val
                            api_duration = dur_val
                            break
                    if not timeline and not plain and results:
                        plain = results[0].get("plainLyrics", "")
                        api_duration = float(results[0].get("duration", 0.0))
            except Exception:
                pass
    except Exception:
        pass

    # Save to cache
    lyrics_cache[track_id] = {
        "timeline": timeline,
        "plain": plain,
        "duration": api_duration,
        "fetched": True
    }
    save_cache()

   
    if current_track == track_id:
        lyrics_timeline = timeline
        plain_lyrics = plain
        if api_duration > 0:
            duration_g = api_duration
        fetch_done = True  

def parse_lrc(lrc_string):
    """Parse [mm:ss.xx] lyric lines into (seconds_float, text) list."""
    timeline = []
    for line in lrc_string.splitlines():
        m = re.match(r'\[(\d{1,3}):(\d{2}(?:\.\d+)?)\](.*)', line)
        if m:
            mins  = int(m.group(1))
            secs  = float(m.group(2))
            text  = m.group(3).strip()
            timeline.append((mins * 60 + secs, text))
    return sorted(timeline, key=lambda x: x[0])

# 3. DISPLAY ENGINE 

last_cols = 0
last_rows = 0

def rgb_to_hsv(r, g, b):
    return colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)

def hsv_to_rgb(h, s, v):
    r, g, b = colorsys.hsv_to_rgb(h, s, v)
    return int(r * 255), int(g * 255), int(b * 255)

def get_accent_color():
    global theme_color_g
    base_r, base_g, base_b = theme_color_g
    h, s, v = rgb_to_hsv(base_r, base_g, base_b)
    # Brighten and saturate for accents
    s_accent = max(0.50, min(1.0, s * 1.8))
    v_accent = max(0.80, min(1.0, v * 5.0))
    return hsv_to_rgb(h, s_accent, v_accent)

def get_background_theme_from_image(img_path_or_url):
    try:
        if img_path_or_url.startswith("file://"):
            from PIL import Image
            local_path = unquote(img_path_or_url[7:])
            img = Image.open(local_path)
        elif img_path_or_url.startswith("http://") or img_path_or_url.startswith("https://"):
            import requests
            from PIL import Image
            from io import BytesIO
            r = requests.get(img_path_or_url, timeout=1.5)
            if r.status_code == 200:
                img = Image.open(BytesIO(r.content))
            else:
                return None
        else:
            return None
            
        img = img.resize((16, 16))
        img = img.convert('RGB')
        pixels = list(img.getdata())
        
        r_sum = sum(p[0] for p in pixels)
        g_sum = sum(p[1] for p in pixels)
        b_sum = sum(p[2] for p in pixels)
        count = len(pixels)
        avg_r = r_sum / count
        avg_g = g_sum / count
        avg_b = b_sum / count
        
        h, s, v = rgb_to_hsv(avg_r, avg_g, avg_b)
        
        # Background should be dark (v = 0.15) 
        s_target = max(0.20, min(s * 1.5, 0.65))
        v_target = 0.15
        
        return hsv_to_rgb(h, s_target, v_target)
    except Exception:
        return None

def get_artwork_url_fallback(artist, title):
    try:
        import urllib.parse
        term = f"{artist} {title}"
        safe_term = urllib.parse.quote_plus(term)
        url = f"https://itunes.apple.com/search?term={safe_term}&limit=1&entity=song"
        session = get_http_session()
        r = session.get(url, timeout=2.0)
        if r.status_code == 200:
            data = r.json()
            results = data.get("results", [])
            if results:
                return results[0].get("artworkUrl100", "")
    except Exception:
        pass
    return ""

def update_theme_color_threaded(art_url, track_id=None, artist=None, title=None):
    global theme_color_g
    color = None
    
    # 1. Try to fetch color from player's reported art_url
    if art_url:
        color = get_background_theme_from_image(art_url)
        
    # 2. Fall back to search API if player didn't provide artwork URL
    if not color and artist and title:
        fetched_art_url = get_artwork_url_fallback(artist, title)
        if fetched_art_url:
            color = get_background_theme_from_image(fetched_art_url)
            
    # 3. Last resort fallback: deterministic hashing
    if not color and track_id:
        import hashlib
        h_val = int(hashlib.md5(track_id.encode('utf-8')).hexdigest(), 16) % 1000 / 1000.0
        color = hsv_to_rgb(h_val, 0.45, 0.12)
        
    if color:
        theme_color_g = color
    else:
        theme_color_g = (45, 15, 22)

def get_bg_color(r, rows):
    global theme_color_g
    base_r, base_g, base_b = theme_color_g
    
    if rows <= 1:
        return base_r, base_g, base_b
        
    pct = r / (rows - 1)
    
    # top is brighter, bottom is darker
    top_r = min(255, int(base_r * 1.3))
    top_g = min(255, int(base_g * 1.3))
    top_b = min(255, int(base_b * 1.3))
    
    bot_r = int(base_r * 0.6)
    bot_g = int(base_g * 0.6)
    bot_b = int(base_b * 0.6)
    
    bg_r = int(top_r + (bot_r - top_r) * pct)
    bg_g = int(top_g + (bot_g - top_g) * pct)
    bg_b = int(top_b + (bot_b - top_b) * pct)
    return bg_r, bg_g, bg_b

def make_empty_row(cols, bg_color):
    bg_r, bg_g, bg_b = bg_color
    return f"\033[48;2;{bg_r};{bg_g};{bg_b}m" + " " * cols + "\033[0m"

def center_line(text, cols, bg_color, fg_color, bold=False):
    bg_r, bg_g, bg_b = bg_color
    fg_r, fg_g, fg_b = fg_color
    style = "\033[1m" if bold else ""
    
    if len(text) > cols - 4:
        text = text[:cols - 7] + "..."
        
    lead_spaces = (cols - len(text)) // 2
    trail_spaces = cols - lead_spaces - len(text)
    
    left_pad = " " * lead_spaces
    right_pad = " " * trail_spaces
    
    return f"\033[48;2;{bg_r};{bg_g};{bg_b}m\033[38;2;{fg_r};{fg_g};{fg_b}m{style}{left_pad}{text}{right_pad}\033[0m"

def center_active_lyric(text, cols, bg_color, lyric_fg, accent_fg):
    if not text.strip():
        return make_empty_row(cols, bg_color)
        
    bg_r, bg_g, bg_b = bg_color
    ly_r, ly_g, ly_b = lyric_fg
    ac_r, ac_g, ac_b = accent_fg
    
    visible_text = f"▶  {text}  ◀"
    if len(visible_text) > cols - 4:
        text = text[:cols - 13] + "..."
        visible_text = f"▶  {text}  ◀"
        
    lead_spaces = (cols - len(visible_text)) // 2
    trail_spaces = cols - lead_spaces - len(visible_text)
    
    left_pad = " " * lead_spaces
    right_pad = " " * trail_spaces
    
    ansi_str = (
        f"\033[38;2;{ac_r};{ac_g};{ac_b}m▶  "
        f"\033[1;38;2;{ly_r};{ly_g};{ly_b}m{text}"
        f"\033[0;38;2;{ac_r};{ac_g};{ac_b}m  ◀"
    )
    
    return f"\033[48;2;{bg_r};{bg_g};{bg_b}m{left_pad}{ansi_str}\033[48;2;{bg_r};{bg_g};{bg_b}m{right_pad}\033[0m"

def centered_separator(cols, bg_color, fg_color, char="─", width_pct=0.7):
    bg_r, bg_g, bg_b = bg_color
    fg_r, fg_g, fg_b = fg_color
    width = int(cols * width_pct)
    if width <= 0:
        return make_empty_row(cols, bg_color)
    line = char * width
    lead_spaces = (cols - width) // 2
    trail_spaces = cols - lead_spaces - width
    left_pad = " " * lead_spaces
    right_pad = " " * trail_spaces
    return f"\033[48;2;{bg_r};{bg_g};{bg_b}m\033[38;2;{fg_r};{fg_g};{fg_b}m{left_pad}{line}{right_pad}\033[0m"

def format_progress_bar_custom(position, duration, cols, bg_color):
    total = duration if duration > 0 else 1.0
    pct = min(position / total, 1.0)
    
    bar_w = min(60, max(20, int(cols * 0.5)))
    filled = int(bar_w * pct)
    
    filled_bar = "━" * filled
    empty_bar = "─" * (bar_w - filled)
    
    elapsed = f"{int(position//60)}:{int(position%60):02d}"
    total_s = f"{int(total//60)}:{int(total%60):02d}" if duration > 0 else "--:--"
    
    bg_r, bg_g, bg_b = bg_color
    
    acc_r, acc_g, acc_b = get_accent_color()
    fill_color = f"\033[38;2;{acc_r};{acc_g};{acc_b};1m"
    knob_color = "\033[38;2;255;255;255;1m"
    empty_color = f"\033[38;2;{bg_r + 30};{bg_g + 10};{bg_b + 15}m"
    time_color = f"\033[38;2;{int(bg_r + (255 - bg_r) * 0.45)};{int(bg_g + (255 - bg_g) * 0.45)};{int(bg_b + (255 - bg_b) * 0.45)}m"
    
    if duration > 0:
        if filled > 0:
            bar_str = f"{fill_color}{filled_bar[:-1]}{knob_color}●{empty_color}{empty_bar}"
        else:
            bar_str = f"{knob_color}●{empty_color}{empty_bar}"
    else:
        bar_str = f"{empty_color}{'─' * bar_w}"
        
    visible_text = f"{elapsed}  {'━' * bar_w}  {total_s}"
    ansi_str = f"{time_color}{elapsed}  {bar_str}{time_color}  {total_s}"
    
    lead_spaces = (cols - len(visible_text)) // 2
    trail_spaces = cols - lead_spaces - len(visible_text)
    
    left_pad = " " * lead_spaces
    right_pad = " " * trail_spaces
    
    return f"\033[48;2;{bg_r};{bg_g};{bg_b}m{left_pad}{ansi_str}\033[48;2;{bg_r};{bg_g};{bg_b}m{right_pad}\033[0m"

def find_active_index(timeline, current_time):
    """Find the index of the lyric line at current_time."""
    active = -1
    for i, (ts, _) in enumerate(timeline):
        if ts < current_time:
            active = i
        elif ts == current_time:
            active = i
            break
        else:
            break
    return active

def render_no_player(cols, rows):
    global last_cols, last_rows
    if cols != last_cols or rows != last_rows:
        sys.stdout.write("\033[2J")
        last_cols, last_rows = cols, rows
        
    screen_lines = [None] * rows
    message = "⏸  No active player found. Start playing something!"
    middle = rows // 2
    
    for r in range(rows):
        bg_color = get_bg_color(r, rows)
        if r == middle:
            screen_lines[r] = center_line(message, cols, bg_color, (200, 140, 160))
        else:
            screen_lines[r] = make_empty_row(cols, bg_color)
            
    buffer = "\n".join(screen_lines)
    sys.stdout.write("\033[H" + buffer)
    sys.stdout.flush()

def cells_to_ansi(cells):
    if not cells:
        return ""
    parts = []
    last_bg = None
    last_fg = None
    last_bold = None
    
    for cell in cells:
        bg = cell['bg']
        fg = cell['fg']
        bold = cell['bold']
        char = cell['char']
        
        if bg != last_bg or fg != last_fg or bold != last_bold:
            style_code = "1;" if bold else ""
            parts.append(f"\033[0;{style_code}48;2;{bg[0]};{bg[1]};{bg[2]};38;2;{fg[0]};{fg[1]};{fg[2]}m")
            last_bg = bg
            last_fg = fg
            last_bold = bold
            
        parts.append(char)
        
    parts.append("\033[0m")
    return "".join(parts)

def render_canvas_row(r, rows, cols, header_h=0, footer_h=0, text=None, is_active=False, lyric_fg=(255, 255, 255), accent_fg=None, bold=False):
    global spectrum_g, cava_running_g
    bg_color = get_bg_color(r, rows)
    bg_r, bg_g, bg_b = bg_color
    
    acc_r, acc_g, acc_b = get_accent_color()
    if accent_fg is None:
        accent_fg = (acc_r, acc_g, acc_b)
        
    bar_fg = (
        int(bg_r + 0.35 * (acc_r - bg_r)),
        int(bg_g + 0.35 * (acc_g - bg_g)),
        int(bg_b + 0.35 * (acc_b - bg_b))
    )
    bar_bg_tint = (
        int(bg_r + 0.25 * (acc_r - bg_r)),
        int(bg_g + 0.25 * (acc_g - bg_g)),
        int(bg_b + 0.25 * (acc_b - bg_b))
    )
    
    if is_active:
        bg_color = (
            min(255, bg_color[0] + 25),
            min(255, bg_color[1] + 15),
            min(255, bg_color[2] + 20)
        )
        bar_bg_tint = (
            min(255, bar_bg_tint[0] + 30),
            min(255, bar_bg_tint[1] + 20),
            min(255, bar_bg_tint[2] + 25)
        )

    cells = [{'char': ' ', 'fg': (0, 0, 0), 'bg': bg_color, 'bold': False} for _ in range(cols)]
    lyric_rows = max(1, rows - header_h - footer_h)
    r_rel = max(0, r - header_h)
    y_from_bot = lyric_rows - 1 - r_rel

    spec = spectrum_g
    if cava_running_g and spec and max(spec) > 0.005:
        bar_w = 2 if cols >= 60 else 1
        gap_w = 1
        step = bar_w + gap_w
        n_bars = max(1, (cols + gap_w) // step)
        total_w = n_bars * step - gap_w
        left_margin = max(0, (cols - total_w) // 2)
        
        for c in range(cols):
            if left_margin <= c < left_margin + total_w:
                c_rel = c - left_margin
                if c_rel % step < bar_w:
                    bar_idx = c_rel // step
                    spec_idx = int((bar_idx / n_bars) * len(spec))
                    val = spec[min(len(spec) - 1, max(0, spec_idx))]
                    bar_h = val * lyric_rows
                    
                    if y_from_bot < int(bar_h):
                        cells[c]['char'] = '█'
                        cells[c]['fg'] = bar_fg
                    elif y_from_bot == int(bar_h):
                        rem = bar_h - int(bar_h)
                        if rem > 0.1:
                            glyphs = [' ', '▂', '▃', '▄', '▅', '▆', '▇', '█']
                            g_idx = min(7, max(0, int(rem * 8)))
                            cells[c]['char'] = glyphs[g_idx]
                            cells[c]['fg'] = bar_fg

    if text and text.strip():
        text_chars = []
        if is_active:
            for ch in "▶  ":
                text_chars.append((ch, accent_fg, False))
            for ch in text:
                text_chars.append((ch, lyric_fg, True))
            for ch in "  ◀":
                text_chars.append((ch, accent_fg, False))
        else:
            for ch in text:
                text_chars.append((ch, lyric_fg, bold))
                
        if len(text_chars) > cols - 4:
            if is_active and len(text_chars) > cols - 13:
                text_chars = text_chars[:cols - 13] + [('.', lyric_fg, True)] * 3 + text_chars[-3:]
            elif len(text_chars) > cols - 7:
                text_chars = text_chars[:cols - 7] + [('.', lyric_fg, bold)] * 3
                
        start_col = (cols - len(text_chars)) // 2
        for i, (ch, fg, b) in enumerate(text_chars):
            c = start_col + i
            if 0 <= c < cols:
                had_bar = (cells[c]['char'] != ' ')
                cells[c]['char'] = ch
                cells[c]['fg'] = fg
                cells[c]['bold'] = b
                if had_bar:
                    cells[c]['bg'] = bar_bg_tint
                else:
                    cells[c]['bg'] = bg_color
                    
    return cells_to_ansi(cells)

def render_ui(position, duration, artist, title):
    global last_cols, last_rows, lyrics_timeline, plain_lyrics, fetch_done
    
    cols, rows = shutil.get_terminal_size()
    if cols < 20 or rows < 4:
        sys.stdout.write("\033[H\033[J")
        print("Terminal too small!")
        sys.stdout.flush()
        return
        
    if cols != last_cols or rows != last_rows:
        sys.stdout.write("\033[2J")
        last_cols, last_rows = cols, rows
        
    screen_lines = [None] * rows
    
    if rows > 15:
        header_height = 3
        footer_height = 3
    elif rows > 8:
        header_height = 1
        footer_height = 1
    else:
        header_height = 0
        footer_height = 0
        
    # Render Header
    if header_height >= 3:
        screen_lines[0] = make_empty_row(cols, get_bg_color(0, rows))
        acc_r, acc_g, acc_b = get_accent_color()
        dim_acc = (int(acc_r * 0.5), int(acc_g * 0.5), int(acc_b * 0.5))
        screen_lines[1] = center_line(f"🎵  {artist}  •  {title}", cols, get_bg_color(1, rows), (255, 255, 255), bold=True)
        screen_lines[2] = centered_separator(cols, get_bg_color(2, rows), dim_acc, char="─", width_pct=0.7)
    elif header_height == 1:
        screen_lines[0] = center_line(f"🎵  {artist}  •  {title}", cols, get_bg_color(0, rows), (255, 255, 255), bold=True)
        
    # Render Footer
    if footer_height >= 3:
        acc_r, acc_g, acc_b = get_accent_color()
        dim_acc = (int(acc_r * 0.5), int(acc_g * 0.5), int(acc_b * 0.5))
        screen_lines[rows - 3] = centered_separator(cols, get_bg_color(rows - 3, rows), dim_acc, char="─", width_pct=0.7)
        screen_lines[rows - 2] = format_progress_bar_custom(position, duration, cols, get_bg_color(rows - 2, rows))
        screen_lines[rows - 1] = make_empty_row(cols, get_bg_color(rows - 1, rows))
    elif footer_height == 1:
        screen_lines[rows - 1] = format_progress_bar_custom(position, duration, cols, get_bg_color(rows - 1, rows))
        
    lyric_rows = rows - header_height - footer_height
    active_row = header_height + (lyric_rows // 2)
    
    visual_position = position
    
    if not fetch_done:
        spinner_chars = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        spinner = spinner_chars[int(time.time() * 10) % len(spinner_chars)]
        fetching_msg = f"{spinner}  Fetching lyrics..."
        for r in range(header_height, rows - footer_height):
            if r == active_row:
                screen_lines[r] = render_canvas_row(r, rows, cols, header_height, footer_height, text=fetching_msg, lyric_fg=(220, 180, 100))
            else:
                screen_lines[r] = render_canvas_row(r, rows, cols, header_height, footer_height)
                
    elif not lyrics_timeline and not plain_lyrics:
        warning_msg = "⚠️  No lyrics found for this track."
        for r in range(header_height, rows - footer_height):
            if r == active_row:
                screen_lines[r] = render_canvas_row(r, rows, cols, header_height, footer_height, text=warning_msg, lyric_fg=(220, 100, 100))
            else:
                screen_lines[r] = render_canvas_row(r, rows, cols, header_height, footer_height)
                
    else:
        if lyrics_timeline:
            lyrics = [item[1] for item in lyrics_timeline]
            active_idx = find_active_index(lyrics_timeline, visual_position)
        else:
            lyrics = plain_lyrics.splitlines()
            if lyrics:
                scroll_speed = 8.0
                active_idx = int(position / scroll_speed) % len(lyrics)
            else:
                active_idx = 0
            
        spacing = 2 if rows >= 20 else 1

        # Guard: active_idx=-1 means before first lyric; treat as 0 for layout
        display_idx = max(0, active_idx)

        # Original clamping logic adapted for spacing multiplier
        top_limit    = header_height + 1
        bottom_limit = rows - footer_height - 2

        target_active_row = active_row
        # Don't scroll above the first lyric
        target_active_row = min(target_active_row, top_limit + display_idx * spacing)
        # Don't scroll below the last lyric
        if len(lyrics) * spacing > lyric_rows:
            target_active_row = max(
                target_active_row,
                bottom_limit - (len(lyrics) - 1 - display_idx) * spacing
            )

        for r in range(header_height, rows - footer_height):
            offset = r - target_active_row

            # In double-spacing mode, only even offsets carry a lyric line
            if spacing == 2:
                if offset % 2 != 0:
                    screen_lines[r] = render_canvas_row(r, rows, cols, header_height, footer_height)
                    continue
                idx = display_idx + offset // 2
            else:
                idx = display_idx + offset

            bg_color = get_bg_color(r, rows)

            if 0 <= idx < len(lyrics):
                text = lyrics[idx]
                is_active = (offset == 0) and (active_idx >= 0)

                if is_active:
                    if lyrics_timeline:
                        ts = lyrics_timeline[idx][0]
                        age = visual_position - ts
                        t = min(1.0, max(0.0, age / 0.25))
                        t = t * t * (3 - 2 * t)
                    else:
                        age = position % 8.0
                        t = min(1.0, max(0.0, age / 0.5))
                        t = t * t * (3 - 2 * t)

                    bg_r, bg_g, bg_b = bg_color
                    opacity_fade = 0.35
                    fg_r_start = int(bg_r + (255 - bg_r) * opacity_fade)
                    fg_g_start = int(bg_g + (255 - bg_g) * opacity_fade)
                    fg_b_start = int(bg_b + (255 - bg_b) * opacity_fade)

                    fg_r = int(fg_r_start + (255 - fg_r_start) * t)
                    fg_g = int(fg_g_start + (255 - fg_g_start) * t)
                    fg_b = int(fg_b_start + (255 - fg_b_start) * t)
                    fg_color = (fg_r, fg_g, fg_b)

                    acc_r, acc_g, acc_b = get_accent_color()
                    acc_r_cur = int(bg_r + (acc_r - bg_r) * (opacity_fade + (1 - opacity_fade) * t))
                    acc_g_cur = int(bg_g + (acc_g - bg_g) * (opacity_fade + (1 - opacity_fade) * t))
                    acc_b_cur = int(bg_b + (acc_b - bg_b) * (opacity_fade + (1 - opacity_fade) * t))
                    accent_fg = (acc_r_cur, acc_g_cur, acc_b_cur)

                    screen_lines[r] = render_canvas_row(r, rows, cols, header_height, footer_height, text=text, is_active=True, lyric_fg=fg_color, accent_fg=accent_fg, bold=True)
                else:
                    distance = abs(idx - display_idx)
                    max_dist = max(1, lyric_rows // (2 * spacing))
                    fade = max(0.12, 1.0 - (distance / max_dist) * 0.75)
                    opacity = 0.48 * fade

                    bg_r, bg_g, bg_b = bg_color
                    fg_r = int(bg_r + (255 - bg_r) * opacity)
                    fg_g = int(bg_g + (255 - bg_g) * opacity)
                    fg_b = int(bg_b + (255 - bg_b) * opacity)
                    fg_color = (fg_r, fg_g, fg_b)

                    screen_lines[r] = render_canvas_row(r, rows, cols, header_height, footer_height, text=text, is_active=False, lyric_fg=fg_color, bold=False)
            else:
                screen_lines[r] = render_canvas_row(r, rows, cols, header_height, footer_height)
                
    for r in range(rows):
        if screen_lines[r] is None:
            screen_lines[r] = make_empty_row(cols, get_bg_color(r, rows))
            
    buffer = "\n".join(screen_lines)
    sys.stdout.write("\033[H" + buffer)
    sys.stdout.flush()

# 4. MAIN LOOP 
player_lock = threading.Lock()
player_active_g = False
is_playing_g = False
artist_cached_g = ""
title_cached_g = ""
position_ref_g = 0.0
time_ref_g = 0.0
duration_cached_g = 0.0
art_url_cached_g = ""

def player_query_worker():
    global player_active_g, is_playing_g, artist_cached_g, title_cached_g
    global position_ref_g, time_ref_g, duration_cached_g, art_url_cached_g
    
    while True:
        try:
            player = get_active_player()
            if player:
                new_artist, new_title, position, duration, art_url = get_player_data(player)
                now_new = time.time()
                
                with player_lock:
                    player_active_g = True
                    is_playing_g = True
                    
                    if new_title:
                        track_changed = (new_artist != artist_cached_g or new_title != title_cached_g)
                        artist_cached_g = new_artist
                        title_cached_g = new_title
                        duration_cached_g = duration
                        art_url_cached_g = art_url
                        
                        if track_changed or time_ref_g == 0:
                            position_ref_g = position
                            time_ref_g = now_new
                        else:
                            # Smooth alignment logic (PLL)
                            current_extrapolated = position_ref_g + (now_new - time_ref_g)
                            if abs(position - current_extrapolated) > 1.2:
                                # User seeked or large discrepancy, jump immediately
                                position_ref_g = position
                            else:
                                # Soft blend to match playerctl's clock without jumps
                                position_ref_g = current_extrapolated + 0.15 * (position - current_extrapolated)
                            time_ref_g = now_new
            else:
                with player_lock:
                    player_active_g = False
                    is_playing_g = False
        except Exception:
            pass
        time.sleep(0.5)

def cava_worker():
    global spectrum_g, cava_running_g
    if not shutil.which("cava"):
        return
    
    num_bars = 48
    config_content = f"""[general]
bars = {num_bars}
framerate = 30
autosens = 1

[output]
method = raw
raw_target = /dev/stdout
data_format = ascii
ascii_max_range = 1000
"""
    try:
        with tempfile.NamedTemporaryFile("w+", delete=True, suffix=".ini") as temp_cfg:
            temp_cfg.write(config_content)
            temp_cfg.flush()
            
            process = subprocess.Popen(
                ["cava", "-p", temp_cfg.name],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1
            )
            cava_running_g = True
            while True:
                line = process.stdout.readline()
                if not line:
                    break
                parts = line.strip().split(";")
                new_spectrum = []
                for p in parts:
                    if p:
                        try:
                            val = min(1.0, max(0.0, int(p) / 1000.0))
                            new_spectrum.append(val)
                        except ValueError:
                            pass
                if new_spectrum:
                    spectrum_g = new_spectrum
    except Exception:
        cava_running_g = False
    finally:
        cava_running_g = False

def main():
    global current_track, lyrics_timeline, plain_lyrics
    global artist_g, title_g, duration_g, fetch_done, lyrics_cache

    load_cache()

    sys.stdout.write("\033[?25l")
    sys.stdout.write("\033[?1049h")
    sys.stdout.flush()

    # Start the player query background thread
    t_query = threading.Thread(target=player_query_worker, daemon=True)
    t_query.start()

    # Start the cava audio visualizer background thread
    t_cava = threading.Thread(target=cava_worker, daemon=True)
    t_cava.start()

    try:
        while True:
            now = time.time()
            cols, rows = shutil.get_terminal_size()

            with player_lock:
                active = player_active_g
                playing = is_playing_g
                artist = artist_cached_g
                title = title_cached_g
                pos_ref = position_ref_g
                t_ref = time_ref_g
                dur = duration_cached_g
                art_url = art_url_cached_g

            if not active:
                render_no_player(cols, rows)
                time.sleep(0.1)
                continue

            track_id = f"{artist}|||{title}"
            if track_id != current_track and title:
                current_track = track_id
                artist_g, title_g = artist, title

                # Fetch theme color in the background
                t_theme = threading.Thread(
                    target=update_theme_color_threaded,
                    args=(art_url, track_id, artist, title),
                    daemon=True
                )
                t_theme.start()

                if track_id in lyrics_cache:
                    cached = lyrics_cache[track_id]
                    lyrics_timeline = cached["timeline"]
                    plain_lyrics    = cached["plain"]
                    duration_g      = cached["duration"] if cached["duration"] > 0 else dur
                    fetch_done      = True
                else:
                    lyrics_timeline = []
                    plain_lyrics    = ""
                    duration_g      = dur
                    fetch_done      = False
                    
                    t = threading.Thread(
                        target=fetch_lyrics_threaded,
                        args=(artist, title, track_id),
                        daemon=True
                    )
                    t.start()

            if playing:
                # Monotonic progression based on actual elapsed wall time since the last reference update
                extrapolated_pos = pos_ref + (now - t_ref)
                if duration_g > 0:
                    extrapolated_pos = min(extrapolated_pos, duration_g)
            else:
                extrapolated_pos = pos_ref

            render_ui(extrapolated_pos, duration_g, artist_g, title_g)
            time.sleep(0.05)

    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write("\033[?1049l")
        sys.stdout.write("\033[?25h")
        sys.stdout.flush()
        print("👋  Bye!")

if __name__ == "__main__":
    main()