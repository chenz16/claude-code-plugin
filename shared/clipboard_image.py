"""
Clipboard image detection and screenshot intent recognition.

Detects if user's voice command mentions a screenshot/image,
grabs the image from clipboard, saves it, and returns the path.
"""

import os
import re
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

_counter = 0


def has_screenshot_intent(text):
    """Check if the text mentions a screenshot or image."""
    text_lower = text.lower()
    return any(kw in text_lower for kw in SCREENSHOT_KEYWORDS)


def grab_clipboard_image():
    """Grab image from clipboard. Returns saved file path or None."""
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
        print(f"[screenshot] Saved: {filename} ({size_kb:.0f} KB)", flush=True)
        return local_path

    except Exception as e:
        print(f"[screenshot] Failed to grab clipboard: {e}", flush=True)
        return None
