"""
Clipboard image detection and screenshot intent recognition.

Detects if user's voice command mentions a screenshot/image,
grabs the image from clipboard or finds the most recent screenshot file.
"""

import glob
import os
import time

SCREENSHOT_KEYWORDS = [
    # Chinese - screenshot actions
    "截图", "截屏", "截个图", "截了个图", "截了一张图", "截了一个图",
    "刚截的", "刚才截的",
    # Chinese - referring to an image
    "图片", "这个图", "这张图", "看看图", "看图", "看下图",
    "这个画面", "当前画面", "屏幕上",
    "看看这个", "帮我看看", "分析这个图", "分析一下图",
    # English
    "screenshot", "screen shot", "screen capture",
    "this image", "this picture", "look at this",
]

SCREENSHOT_DIR = "/tmp/claude-screenshots"
if os.name == "nt":
    SCREENSHOT_DIR = os.path.join(os.environ.get("TEMP", "C:\\Temp"), "claude-screenshots")

# Also search system screenshot folders
EXTRA_SCREENSHOT_DIRS = []
if os.name == "nt":
    # Windows: Screenshots from Win+Shift+S go to clipboard, but
    # Win+PrintScreen saves to Pictures/Screenshots
    pictures = os.path.join(os.environ.get("USERPROFILE", ""), "Pictures", "Screenshots")
    if os.path.isdir(pictures):
        EXTRA_SCREENSHOT_DIRS.append(pictures)
    # OneDrive screenshots
    onedrive = os.environ.get("OneDrive", "")
    if onedrive:
        od_screenshots = os.path.join(onedrive, "Pictures", "Screenshots")
        if os.path.isdir(od_screenshots):
            EXTRA_SCREENSHOT_DIRS.append(od_screenshots)

MAX_AGE_SECONDS = 120  # Only consider screenshots from last 2 minutes

_counter = 0


def has_screenshot_intent(text):
    """Check if the text mentions a screenshot or image."""
    text_lower = text.lower()
    return any(kw in text_lower for kw in SCREENSHOT_KEYWORDS)


def _grab_from_clipboard():
    """Try to grab image directly from clipboard. Returns saved path or None."""
    global _counter
    try:
        from PIL import ImageGrab
        img = ImageGrab.grabclipboard()
        if img is None or not hasattr(img, 'tobytes'):
            return None

        os.makedirs(SCREENSHOT_DIR, exist_ok=True)
        _counter += 1
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        filename = f"screenshot_{timestamp}_{_counter}.png"
        local_path = os.path.join(SCREENSHOT_DIR, filename)

        img.save(local_path, "PNG")
        size_kb = os.path.getsize(local_path) / 1024
        print(f"[screenshot] Clipboard image saved: {filename} ({size_kb:.0f} KB)", flush=True)
        return local_path
    except Exception:
        return None


def _find_recent_screenshot():
    """Find the most recent screenshot file (within MAX_AGE_SECONDS).
    Searches our temp dir and system screenshot folders."""
    now = time.time()
    best_path = None
    best_mtime = 0

    search_dirs = [SCREENSHOT_DIR] + EXTRA_SCREENSHOT_DIRS
    for d in search_dirs:
        if not os.path.isdir(d):
            continue
        for pattern in ["*.png", "*.jpg", "*.jpeg", "*.bmp"]:
            for f in glob.glob(os.path.join(d, pattern)):
                try:
                    mtime = os.path.getmtime(f)
                    age = now - mtime
                    if age <= MAX_AGE_SECONDS and mtime > best_mtime:
                        best_mtime = mtime
                        best_path = f
                except Exception:
                    continue

    if best_path:
        age = int(now - best_mtime)
        print(f"[screenshot] Found recent file ({age}s ago): {os.path.basename(best_path)}", flush=True)
    return best_path


def grab_screenshot():
    """Get the most relevant screenshot: clipboard first, then recent files.
    Returns file path or None."""
    # 1. Try clipboard (image might still be there)
    path = _grab_from_clipboard()
    if path:
        return path

    # 2. Fall back to most recent screenshot file (within 2 minutes)
    path = _find_recent_screenshot()
    if path:
        return path

    print("[screenshot] No screenshot found in clipboard or recent files.", flush=True)
    return None
