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
from urllib.parse import unquote

warnings.filterwarnings("ignore", category=DeprecationWarning)

# ─── GLOBALS ────────────────────────────────────────────────
lyrics_timeline = []
plain_lyrics    = ""
artist_g        = ""
title_g         = ""
duration_g      = 0.0
fetch_done      = True
current_track   = ""
lyrics_cache    = {}
theme_color_g   = (45, 15, 22)

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

# ─── 1. PLAYER FUNCTIONS ────────────────────────────────────

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
        "User-Agent": "lyricT/1.0.0 (https://github.com/satvik/lyricT)"
    }

    # PRIMARY: lrclib with correct param names
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
            # Only try secondary search if exact match returned 404
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

    # Only update active globals if this is still the current track
    if current_track == track_id:
        lyrics_timeline = timeline
        plain_lyrics = plain
        if api_duration > 0:
            duration_g = api_duration
        fetch_done = True  # Mark fetch complete only for current track

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

# ─── 3. DISPLAY ENGINE ───────────────────────────────────────

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
        
        # Background should be dark (v = 0.15) and moderately saturated
        s_target = max(0.20, min(s * 1.5, 0.65))
        v_target = 0.15
        
        return hsv_to_rgb(h, s_target, v_target)
    except Exception:
        return None

def update_theme_color_threaded(art_url):
    global theme_color_g
    if not art_url:
        theme_color_g = (45, 15, 22)
        return
        
    color = get_background_theme_from_image(art_url)
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
    
    # top is 30% brighter, bottom is 40% darker
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
            bg_color = get_bg_color(r, rows)
            if r == active_row:
                screen_lines[r] = center_line(fetching_msg, cols, bg_color, (220, 180, 100))
            else:
                screen_lines[r] = make_empty_row(cols, bg_color)
                
    elif not lyrics_timeline and not plain_lyrics:
        warning_msg = "⚠️  No lyrics found for this track."
        for r in range(header_height, rows - footer_height):
            bg_color = get_bg_color(r, rows)
            if r == active_row:
                screen_lines[r] = center_line(warning_msg, cols, bg_color, (220, 100, 100))
            else:
                screen_lines[r] = make_empty_row(cols, bg_color)
                
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
        target_active_row = active_row
        
        for r in range(header_height, rows - footer_height):
            offset = r - target_active_row
            
            if spacing == 2:
                if offset % 2 == 0:
                    idx = active_idx + offset // 2
                else:
                    screen_lines[r] = make_empty_row(cols, get_bg_color(r, rows))
                    continue
            else:
                idx = active_idx + offset
                
            bg_color = get_bg_color(r, rows)
            
            if 0 <= idx < len(lyrics):
                text = lyrics[idx]
                if idx == active_idx:
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
                    
                    fg_r_end = 255
                    fg_g_end = 255
                    fg_b_end = 255
                    
                    fg_r = int(fg_r_start + (fg_r_end - fg_r_start) * t)
                    fg_g = int(fg_g_start + (fg_g_end - fg_g_start) * t)
                    fg_b = int(fg_b_start + (fg_b_end - fg_b_start) * t)
                    fg_color = (fg_r, fg_g, fg_b)
                    
                    acc_r, acc_g, acc_b = get_accent_color()
                    acc_r_start = int(bg_r + (acc_r - bg_r) * opacity_fade)
                    acc_g_start = int(bg_g + (acc_g - bg_g) * opacity_fade)
                    acc_b_start = int(bg_b + (acc_b - bg_b) * opacity_fade)
                    acc_r_cur = int(acc_r_start + (acc_r - acc_r_start) * t)
                    acc_g_cur = int(acc_g_start + (acc_g - acc_g_start) * t)
                    acc_b_cur = int(acc_b_start + (acc_b - acc_b_start) * t)
                    accent_fg = (acc_r_cur, acc_g_cur, acc_b_cur)
                    
                    bg_color_active = (
                        min(255, bg_r + 25),
                        min(255, bg_g + 15),
                        min(255, bg_b + 20)
                    )
                    screen_lines[r] = center_active_lyric(text, cols, bg_color_active, fg_color, accent_fg)
                else:
                    distance = abs(idx - active_idx)
                    max_dist = max(1, lyric_rows // (2 * spacing))
                    fade = max(0.12, 1.0 - (distance / max_dist) * 0.8)
                    opacity = 0.50 * fade
                    
                    bg_r, bg_g, bg_b = bg_color
                    fg_r = int(bg_r + (255 - bg_r) * opacity)
                    fg_g = int(bg_g + (255 - bg_g) * opacity)
                    fg_b = int(bg_b + (255 - bg_b) * opacity)
                    fg_color = (fg_r, fg_g, fg_b)
                    
                    screen_lines[r] = center_line(text, cols, bg_color, fg_color, bold=False)
            else:
                screen_lines[r] = make_empty_row(cols, get_bg_color(r, rows))
                
    for r in range(rows):
        if screen_lines[r] is None:
            screen_lines[r] = make_empty_row(cols, get_bg_color(r, rows))
            
    buffer = "\n".join(screen_lines)
    sys.stdout.write("\033[H" + buffer)
    sys.stdout.flush()

# ─── 4. MAIN LOOP ────────────────────────────────────────────

def main():
    global current_track, lyrics_timeline, plain_lyrics
    global artist_g, title_g, duration_g, fetch_done, lyrics_cache

    load_cache()

    sys.stdout.write("\033[?25l")
    sys.stdout.write("\033[?1049h")
    sys.stdout.flush()
    
    player = None
    last_query = 0.0
    artist_cached = ""
    title_cached = ""
    position_cached = 0.0
    duration_cached = 0.0
    art_url_cached = ""
    last_position_update = 0.0
    is_playing = False

    try:
        while True:
            now = time.time()
            cols, rows = shutil.get_terminal_size()
            
            if now - last_query > 0.5:
                player = get_active_player()
                if player:
                    is_playing = True
                    new_artist, new_title, position, duration, art_url = get_player_data(player)
                    if new_title:
                        artist_cached = new_artist
                        title_cached = new_title
                        position_cached = position
                        duration_cached = duration
                        art_url_cached = art_url
                        last_position_update = now
                else:
                    is_playing = False
                last_query = now

            if not player:
                render_no_player(cols, rows)
                time.sleep(0.1)
                continue

            track_id = f"{artist_cached}|||{title_cached}"

            if track_id != current_track and title_cached:
                current_track = track_id
                artist_g, title_g = artist_cached, title_cached
                
                # Fetch theme color in the background
                t_theme = threading.Thread(
                    target=update_theme_color_threaded,
                    args=(art_url_cached,),
                    daemon=True
                )
                t_theme.start()

                if track_id in lyrics_cache:
                    cached = lyrics_cache[track_id]
                    lyrics_timeline = cached["timeline"]
                    plain_lyrics    = cached["plain"]
                    duration_g      = cached["duration"] if cached["duration"] > 0 else duration_cached
                    fetch_done      = True
                else:
                    lyrics_timeline = []
                    plain_lyrics    = ""
                    duration_g      = duration_cached
                    fetch_done      = False
                    
                    t = threading.Thread(
                        target=fetch_lyrics_threaded,
                        args=(artist_cached, title_cached, track_id),
                        daemon=True
                    )
                    t.start()

            if is_playing:
                dt = now - last_position_update
                extrapolated_pos = position_cached + dt
                if duration_g > 0:
                    extrapolated_pos = min(extrapolated_pos, duration_g)
            else:
                extrapolated_pos = position_cached

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