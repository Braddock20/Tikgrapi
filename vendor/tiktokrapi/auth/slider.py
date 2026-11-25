"""Local slider captcha solver via OpenCV template matching.

TikTok's puzzle slider: BG image has a notched slot, slider piece needs to fit it.
Find slot's X offset by edge-detecting the bg, then template-matching the piece.

Free, fast (~50ms), high accuracy on TikTok's specific puzzle style.
Requires `opencv-python-headless` + `numpy`.
"""
from __future__ import annotations

from typing import Optional

try:
    import cv2  # type: ignore
    import numpy as np  # type: ignore
    OPENCV_AVAILABLE = True
except ImportError:
    OPENCV_AVAILABLE = False


def solve_slider_local(bg_bytes: bytes, piece_bytes: bytes) -> int:
    """Return X offset (px) where slider piece fits the bg slot.

    Args:
        bg_bytes: PNG/JPG bytes of the background (with notched slot)
        piece_bytes: PNG/JPG bytes of the slider piece

    Returns:
        X offset in px (assumes Y is fixed by TikTok's UI)
    """
    if not OPENCV_AVAILABLE:
        raise ImportError(
            "opencv-python-headless not installed. "
            "pip install 'tiktokrapi[captcha]' or pip install opencv-python-headless numpy"
        )

    bg_arr = cv2.imdecode(np.frombuffer(bg_bytes, np.uint8), cv2.IMREAD_GRAYSCALE)
    piece_arr = cv2.imdecode(np.frombuffer(piece_bytes, np.uint8), cv2.IMREAD_GRAYSCALE)

    bg_edges = cv2.Canny(bg_arr, 100, 200)
    piece_edges = cv2.Canny(piece_arr, 100, 200)

    # Crop piece to non-zero region
    coords = cv2.findNonZero(piece_edges)
    if coords is None:
        raise ValueError("piece image had no edges — not a valid slider captcha")
    x, y, w, h = cv2.boundingRect(coords)
    piece_crop = piece_edges[y : y + h, x : x + w]

    res = cv2.matchTemplate(bg_edges, piece_crop, cv2.TM_CCOEFF_NORMED)
    _, _, _, max_loc = cv2.minMaxLoc(res)
    return max_loc[0]
