import os
import glob
import logging
import yt_dlp
from pathlib import Path

logger = logging.getLogger(__name__)

DOWNLOADS_DIR = Path("downloads")
DOWNLOADS_DIR.mkdir(exist_ok=True)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# ─── Search ───────────────────────────────────────────────────────────────────

def search_youtube(query: str, max_results: int = 8) -> list[dict]:
    """Search YouTube, returns list of result dicts."""
    if "uzbek" not in query.lower() and "o'zbek" not in query.lower():
        search_term = f"{query} uzbekcha qoshiq"
    else:
        search_term = query

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "http_headers": HEADERS,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(
                f"ytsearch{max_results}:{search_term}", download=False
            )
            if info and "entries" in info:
                results = []
                for entry in info["entries"]:
                    if not entry:
                        continue
                    vid_id = entry.get("id", "")
                    if not vid_id:
                        continue
                    results.append({
                        "title":    entry.get("title", "Nomsiz qo'shiq"),
                        "url":      f"https://www.youtube.com/watch?v={vid_id}",
                        "duration": entry.get("duration", 0),
                        "id":       vid_id,
                        "channel":  entry.get("uploader") or entry.get("channel") or "",
                    })
                return results
    except Exception as e:
        logger.error(f"YouTube search error: {e}")
    return []


def get_video_info(url: str) -> dict | None:
    """Get title and duration for a direct URL."""
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "http_headers": HEADERS,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if info:
                return {
                    "title":    info.get("title", "Nomsiz"),
                    "duration": info.get("duration", 0),
                    "id":       info.get("id", "direct"),
                    "url":      url,
                    "channel":  info.get("uploader") or info.get("channel") or "",
                }
    except Exception as e:
        logger.error(f"get_video_info error: {e}")
    return None


# ─── Download ─────────────────────────────────────────────────────────────────

def _find_downloaded_file(base_path: str) -> str | None:
    """
    Find any audio/video file that starts with base_path.
    Uses glob — safe and correct (avoids rstrip character-set bug).
    """
    matches = sorted(
        glob.glob(base_path + ".*"),
        key=os.path.getsize,
        reverse=True,   # largest first (prefer full quality)
    )
    for f in matches:
        if os.path.isfile(f) and os.path.getsize(f) > 0:
            logger.info(f"Found downloaded file: {f}")
            return f
    return None


def download_audio(video_url: str, output_filename: str) -> str | None:
    """
    Download audio from any yt-dlp supported URL.
    Strategy:
      1. Try with ffmpeg → mp3
      2. Fallback: native format (m4a / webm / opus — no ffmpeg needed)
    Returns the downloaded file path, or None.
    """
    base_path = str(DOWNLOADS_DIR / output_filename)
    output_template = base_path + ".%(ext)s"

    # ── Stage 1: ffmpeg → mp3 ─────────────────────────────────────────────────
    opts_mp3 = {
        "format": "bestaudio/best",
        "outtmpl": output_template,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 30,
        "retries": 3,
        "http_headers": HEADERS,
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "128",
        }],
    }
    try:
        with yt_dlp.YoutubeDL(opts_mp3) as ydl:
            ydl.download([video_url])
        result = _find_downloaded_file(base_path)
        if result:
            logger.info(f"Stage 1 (mp3) success: {result}")
            return result
    except Exception as e:
        logger.warning(f"Stage 1 (mp3) failed: {e}")

    # ── Stage 2: native audio (no ffmpeg) ─────────────────────────────────────
    # Telegram natively plays .m4a and .mp3. It treats .webm as a document.
    # Therefore, we MUST prioritize m4a (itag 140 is YouTube's standard m4a).
    opts_native = {
        "format": "140/bestaudio[ext=m4a]/bestaudio",
        "outtmpl": output_template,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 30,
        "retries": 3,
        "http_headers": HEADERS,
    }
    try:
        with yt_dlp.YoutubeDL(opts_native) as ydl:
            ydl.download([video_url])
        result = _find_downloaded_file(base_path)
        if result:
            logger.info(f"Stage 2 (native m4a) success: {result}")
            return result
    except Exception as e:
        logger.warning(f"Stage 2 (native m4a) failed: {e}")

    logger.error(f"All download attempts failed for: {video_url}")
    return None


# ─── Cleanup ──────────────────────────────────────────────────────────────────

def cleanup_file(file_path: str | None):
    """Safely delete a temp file."""
    if not file_path:
        return
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
    except Exception as e:
        logger.error(f"Cleanup error: {e}")
