"""The frame renderer.

Pillow draws the composition, FFmpeg encodes it. The renderer is built for
a small machine:

* every layer is prepared once (photo framing, paper texture, decoration)
  and then re-used for all the frames it appears in
* layers are stored as an RGB image plus an alpha mask, so a frame can be
  assembled with ``paste`` instead of full-canvas alpha compositing
* text is rendered per reveal step and cached, not per frame
* nothing is written to disk except the final video
"""
from __future__ import annotations

import math
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from video_engine.animation.effects import (
    FADE_IN,
    FADE_OUT,
    ROTATE,
    ZOOM,
    ElementState,
    clip_text,
    element_state,
)
from video_engine.animation.transitions import slide_offset, transition_weight
from video_engine.assets.fonts import load_font
from video_engine.design.decorations import (
    drop_shadow,
    hex_to_rgba,
    paper_texture,
    render_decoration,
    torn_edge_mask,
    with_alpha,
)
from video_engine.design.model import (
    KIND_DECORATION,
    KIND_IMAGE,
    KIND_SURFACE,
    KIND_TEXT,
    Composition,
    Element,
    Page,
)
from video_engine.rendering.profiles import RenderProfile

Image.MAX_IMAGE_PIXELS = 120_000_000

SHADOW_PAD = 48


# --- layer model ---------------------------------------------------------
@dataclass
class Layer:
    """A prepared, reusable drawable."""

    element: Element
    rgb: Image.Image
    mask: Image.Image
    pad: int = 0
    zoom_source: Image.Image | None = None
    shadow: bool = False
    text_signature: tuple | None = None

    @property
    def width(self) -> int:
        return self.rgb.width

    @property
    def height(self) -> int:
        return self.rgb.height


@dataclass
class PreparedPage:
    """A page with all of its layers resolved."""

    page: Page
    background: Image.Image | None = None
    video: "VideoFrameSource | None" = None
    layers: list[Layer] = field(default_factory=list)
    duration: float = 0.0


class VideoFrameSource:
    """Decoded frames of a background video, on disk as small JPEGs.

    Frames are decoded once into a temporary directory and read lazily, so a
    long video never sits in memory.
    """

    def __init__(self, path: str | Path, width: int, height: int, fps: int) -> None:
        self.path = Path(path)
        self.width = width
        self.height = height
        self.fps = fps
        self.temp_dir = Path(tempfile.mkdtemp(prefix="ve-vid-"))
        self.count = 0
        self._extract()

    def _extract(self) -> None:
        binary = shutil.which("ffmpeg")
        if not binary:
            return
        pattern = self.temp_dir / "f%05d.jpg"
        args = [
            binary,
            "-y",
            "-nostdin",
            "-loglevel",
            "error",
            "-i",
            str(self.path),
            "-vf",
            f"scale={self.width}:{self.height}:force_original_aspect_ratio=increase,"
            f"crop={self.width}:{self.height}",
            "-q:v",
            "4",
            "-r",
            str(self.fps),
            str(pattern),
        ]
        try:
            subprocess.run(args, capture_output=True, timeout=900, check=False)
        except (subprocess.TimeoutExpired, OSError):
            pass
        self.count = len(list(self.temp_dir.glob("f*.jpg")))

    def frame(self, index: int) -> Image.Image | None:
        if self.count <= 0:
            return None
        index = index % self.count
        path = self.temp_dir / f"f{index + 1:05d}.jpg"
        if not path.exists():
            return None
        try:
            with Image.open(path) as image:
                return image.convert("RGB")
        except (OSError, ValueError):
            return None

    def cleanup(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)


# --- helpers -------------------------------------------------------------
def cover_crop(image: Image.Image, width: int, height: int) -> Image.Image:
    """Scale an image to cover the box and crop the overflow."""
    if width <= 0 or height <= 0:
        return image
    ratio = max(width / image.width, height / image.height)
    new_size = (max(1, int(image.width * ratio)), max(1, int(image.height * ratio)))
    resized = image.resize(new_size, Image.LANCZOS)
    left = (resized.width - width) // 2
    top = (resized.height - height) // 2
    return resized.crop((left, top, left + width, top + height))


def fit_inside(image: Image.Image, width: int, height: int) -> Image.Image:
    ratio = min(width / image.width, height / image.height)
    new_size = (max(1, int(image.width * ratio)), max(1, int(image.height * ratio)))
    return image.resize(new_size, Image.LANCZOS)


def split_rgba(image: Image.Image) -> tuple[Image.Image, Image.Image]:
    """Split an RGBA image into an RGB body and an alpha mask."""
    if image.mode != "RGBA":
        image = image.convert("RGBA")
    rgb = image.convert("RGB")
    mask = image.getchannel("A")
    return rgb, mask


def element_pixel_box(element: Element, width: int, height: int) -> tuple[int, int, int, int]:
    left = int(round((element.x - element.width / 2) * width))
    top = int(round((element.y - element.height / 2) * height))
    box_w = max(4, int(round(element.width * width)))
    box_h = max(4, int(round(element.height * height)))
    return left, top, box_w, box_h


def _is_dark(colour: str) -> bool:
    """True when a colour is dark enough to need light text on top."""
    value = colour.lstrip("#")
    if len(value) != 6:
        return False
    try:
        r, g, b = int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)
    except ValueError:
        return False
    luminance = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255
    return luminance < 0.45


# --- the renderer --------------------------------------------------------
class SceneRenderer:
    """Turns a composition into frames."""

    def __init__(
        self,
        composition: Composition,
        profile: RenderProfile,
        *,
        work_dir: str | Path | None = None,
        assets_root: str | Path | None = None,
    ) -> None:
        self.composition = composition
        self.profile = profile
        self.width = profile.width
        self.height = profile.height
        self.work_dir = Path(work_dir) if work_dir else None
        self.assets_root = Path(assets_root) if assets_root else None
        self.pages: list[PreparedPage] = []
        self._text_cache: dict[tuple, tuple[Image.Image, Image.Image]] = {}
        self._photo_cache: dict[tuple, Image.Image] = {}
        self._surface_cache: dict[tuple, Image.Image] = {}
        self._rng = np.random.default_rng(0)

    # -- preparation -----------------------------------------------------
    def prepare(self) -> None:
        for page in self.composition.pages:
            prepared = PreparedPage(page=page, duration=page.duration)
            background = page.background or self.composition.background
            prepared.background = self._prepare_background(background)
            for element in sorted(page.elements, key=lambda e: e.z_index):
                layer = self._prepare_element(element)
                if layer is not None:
                    prepared.layers.append(layer)
            self.pages.append(prepared)

    def _prepare_background(self, background: dict[str, Any]) -> Image.Image | None:
        kind = (background or {}).get("type")
        if kind == "image":
            path = background.get("path")
            if path and Path(path).exists():
                try:
                    with Image.open(path) as image:
                        base = cover_crop(image.convert("RGB"), self.width, self.height)
                    return self._tint(base)
                except (OSError, ValueError):
                    return None
        if kind == "video":
            path = background.get("path")
            if path and Path(path).exists():
                source = VideoFrameSource(path, self.width, self.height, self.profile.fps)
                if source.count:
                    frame = source.frame(0)
                    if frame is not None:
                        return self._tint(frame)
                source.cleanup()
            return None
        colour = (background or {}).get("colour") or self.composition.palette.get("paper", "#F3EFE6")
        return Image.new("RGB", (self.width, self.height), hex_to_rgba(colour, 255))

    def _tint(self, image: Image.Image) -> Image.Image:
        """Pull a background towards the palette so the page feels like one design.

        A dark palette needs a dark ground, otherwise light text on it
        disappears — so the tint is much stronger for dark palettes.
        """
        paper = self.composition.palette.get("paper", "#F3EFE6")
        overlay = Image.new("RGB", image.size, hex_to_rgba(paper, 255))
        strength = 0.78 if _is_dark(paper) else 0.22
        return Image.blend(image, overlay, strength)

    def _prepare_element(self, element: Element) -> Layer | None:
        if element.kind == KIND_TEXT:
            return self._prepare_text(element)
        if element.kind == KIND_IMAGE:
            return self._prepare_image(element)
        if element.kind == KIND_SURFACE:
            return self._prepare_surface(element)
        if element.kind == KIND_DECORATION:
            return self._prepare_decoration(element)
        return None

    # -- text ------------------------------------------------------------
    def _prepare_text(self, element: Element) -> Layer | None:
        payload = element.payload or {}
        lines = payload.get("lines") or []
        if not lines:
            return None
        left, top, box_w, box_h = element_pixel_box(element, self.width, self.height)
        rgb, mask = self._render_text(element, lines, box_w)
        return Layer(element=element, rgb=rgb, mask=mask, text_signature=tuple(lines))

    def _render_text(self, element: Element, lines: list[str], box_w: int) -> tuple[Image.Image, Image.Image]:
        signature = (element.uid, tuple(lines), box_w)
        cached = self._text_cache.get(signature)
        if cached is not None:
            return cached

        payload = element.payload or {}
        size = int(payload.get("font_size") or 48)
        weight = int(payload.get("font_weight") or 400)
        font_path = payload.get("font_path") or ""
        tracking = float(payload.get("tracking") or 0.0)
        align = payload.get("align") or "left"
        colour = payload.get("colour") or "#222222"
        shadow = bool(payload.get("shadow"))
        underline = bool(payload.get("underline"))
        highlight = payload.get("highlight")

        line_heights = payload.get("line_heights") or [int(size * 1.3)] * len(lines)
        total_height = max(1, sum(line_heights)) + 12

        image = Image.new("RGBA", (max(8, box_w), total_height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        try:
            font = load_font(font_path, size, weight)
        except (OSError, ValueError):
            font = ImageFont.load_default()

        offset = 0
        for index, line in enumerate(lines):
            line_height = line_heights[index] if index < len(line_heights) else int(size * 1.3)
            width = int(font.getlength(line)) + int(tracking * max(0, len(line) - 1))
            if align == "center":
                x = (box_w - width) // 2
            elif align == "right":
                x = box_w - width
            else:
                x = 0
            if highlight:
                draw.rectangle(
                    [x - 6, offset + int(size * 0.08), x + width + 6, offset + int(size * 1.02)],
                    fill=highlight,
                )
            if shadow:
                _draw_line(draw, font, line, x + 3, offset + 4, tracking, "#00000038")
            _draw_line(draw, font, line, x, offset, tracking, colour)
            if underline and index == len(lines) - 1:
                draw.line(
                    [x, offset + int(size * 1.12), x + width, offset + int(size * 1.12)],
                    fill=colour,
                    width=max(2, size // 22),
                )
            offset += line_height

        rgb, mask = split_rgba(image)
        self._text_cache[signature] = (rgb, mask)
        return rgb, mask

    # -- images ----------------------------------------------------------
    def _prepare_image(self, element: Element) -> Layer | None:
        payload = element.payload or {}
        path = payload.get("path")
        if not path or not Path(path).exists():
            return None
        left, top, box_w, box_h = element_pixel_box(element, self.width, self.height)
        frame = payload.get("frame", "plain")

        key = (str(path), box_w, box_h, frame, payload.get("caption", ""))
        cached = self._photo_cache.get(key)
        if cached is None:
            try:
                with Image.open(path) as source:
                    photo = cover_crop(source.convert("RGB"), box_w, box_h)
            except (OSError, ValueError):
                return None
            framed = self._frame_photo(photo, frame, payload, box_w, box_h)
            cached = framed
            self._photo_cache[key] = cached

        zoom_source = None
        if (element.animation or {}).get("type") == ZOOM:
            zoom_source = cached.resize(
                (int(cached.width * 1.12), int(cached.height * 1.12)), Image.LANCZOS
            )

        rgb, mask = split_rgba(cached)
        return Layer(element=element, rgb=rgb, mask=mask, zoom_source=zoom_source)

    def _frame_photo(
        self,
        photo: Image.Image,
        frame: str,
        payload: dict[str, Any],
        box_w: int,
        box_h: int,
    ) -> Image.Image:
        palette = self.composition.palette
        if frame == "polaroid":
            bottom = max(28, int(box_h * 0.18))
            inset = max(6, int(box_w * 0.05))
            photo_h = max(8, box_h - bottom)
            if photo_h != photo.height:
                photo = cover_crop(photo, box_w - inset * 2, photo_h)
            canvas = Image.new("RGBA", (box_w, box_h), hex_to_rgba(palette.get("surface", "#FFFFFF"), 255))
            canvas.paste(photo, (inset, inset))
            draw = ImageDraw.Draw(canvas)
            caption = payload.get("caption") or ""
            if caption:
                try:
                    font = ImageFont.truetype(palette.get("primary_path", ""), max(14, int(box_w * 0.045)))
                except (OSError, ValueError):
                    font = ImageFont.load_default()
                draw.text(
                    (inset + 2, box_h - int(bottom * 0.82)),
                    caption,
                    font=font,
                    fill=hex_to_rgba(palette.get("ink", "#222222"), 220),
                )
            return canvas
        if frame == "plain":
            canvas = Image.new("RGBA", (box_w, box_h), (0, 0, 0, 0))
            canvas.paste(photo, (0, 0))
            draw = ImageDraw.Draw(canvas)
            draw.rectangle([0, 0, box_w - 1, box_h - 1], outline=hex_to_rgba(palette.get("frame", "#CCCCCC"), 255), width=3)
            return canvas
        if frame == "tape":
            canvas = Image.new("RGBA", (box_w, box_h), (0, 0, 0, 0))
            canvas.paste(photo, (0, 0))
            tape = render_decoration(
                "tape",
                (int(box_w * 0.45), int(box_h * 0.12)),
                _PaletteView(self.composition.palette),
                _SeededRandom(1),
            )
            if tape is not None:
                canvas.alpha_composite(tape, (int(box_w * 0.28), -int(box_h * 0.04)))
            return canvas
        return photo.convert("RGBA")

    # -- surfaces --------------------------------------------------------
    def _prepare_surface(self, element: Element) -> Layer | None:
        payload = element.payload or {}
        style = payload.get("style", "card")
        colour = payload.get("colour") or self.composition.palette.get("paper", "#F0EBE0")
        left, top, box_w, box_h = element_pixel_box(element, self.width, self.height)
        key = (style, colour, box_w, box_h, bool(payload.get("texture")), bool(payload.get("ruled")))
        cached = self._surface_cache.get(key)
        if cached is None:
            cached = self._draw_surface(element, style, colour, box_w, box_h, payload)
            self._surface_cache[key] = cached
        if cached is None:
            return None

        rgb, mask = split_rgba(cached)
        pad = SHADOW_PAD if payload.get("shadow") and style in {"paper", "torn", "card"} else 0
        if pad:
            canvas = Image.new("RGBA", (cached.width + pad * 2, cached.height + pad * 2), (0, 0, 0, 0))
            shadow = drop_shadow(cached, offset=(3, 9), blur=30, opacity=0.16)
            canvas.alpha_composite(shadow, (pad, pad))
            canvas.alpha_composite(cached, (pad, pad))
            rgb, mask = split_rgba(canvas)
        return Layer(element=element, rgb=rgb, mask=mask, pad=pad)

    def _draw_surface(
        self,
        element: Element,
        style: str,
        colour: str,
        box_w: int,
        box_h: int,
        payload: dict[str, Any],
    ) -> Image.Image | None:
        palette = self.composition.palette
        rng = _SeededRandom(abs(hash(element.x * 1000 + element.y)) % 100000)

        if style == "rule":
            image = Image.new("RGBA", (box_w, max(2, box_h)), hex_to_rgba(colour, 255))
            return image
        if style == "gradient":
            image = Image.new("RGBA", (box_w, box_h), (0, 0, 0, 0))
            draw = ImageDraw.Draw(image)
            direction = payload.get("direction", "down")
            base = hex_to_rgba(colour, 255)
            for y in range(box_h):
                p = y / max(1, box_h - 1)
                if direction == "up":
                    # Strongest at the bottom: the scrim rises from the edge.
                    alpha = int(255 * p**1.6)
                else:
                    # Strongest at the top: the image fades into the page.
                    alpha = int(255 * (1 - p) ** 1.6)
                draw.line([(0, y), (box_w, y)], fill=(base[0], base[1], base[2], alpha))
            return image
        if style == "film":
            image = render_decoration("film_border", (box_w, box_h), _PaletteView(palette), rng)
            return image

        if style in {"paper", "torn"}:
            texture = paper_texture((box_w, box_h), colour, rng, fibres=bool(payload.get("texture")))
            if style == "torn":
                mask = torn_edge_mask((box_w, box_h), rng, amplitude=max(6, box_w // 40))
                texture.putalpha(Image.composite(texture.getchannel("A"), Image.new("L", (box_w, box_h), 0), mask))
            if payload.get("ruled"):
                # "RGBA" mode makes the ink blend with the paper instead of
                # replacing it (which would punch a hole in the page).
                draw = ImageDraw.Draw(texture, "RGBA")
                line_colour = hex_to_rgba(palette.get("frame", "#CCCCCC"), 190)
                step = max(24, box_h // 16)
                for y in range(step, box_h - step // 2, step):
                    draw.line([(int(box_w * 0.09), y), (int(box_w * 0.91), y)], fill=line_colour, width=2)
            return texture

        # "card" — a flat sheet with a soft edge.
        image = Image.new("RGBA", (box_w, box_h), hex_to_rgba(colour, 255))
        draw = ImageDraw.Draw(image)
        draw.rectangle([0, 0, box_w - 1, box_h - 1], outline=hex_to_rgba(palette.get("frame", "#DDD"), 120), width=2)
        return image

    # -- decorations -----------------------------------------------------
    def _prepare_decoration(self, element: Element) -> Layer | None:
        payload = element.payload or {}
        name = payload.get("decoration", "star")
        left, top, box_w, box_h = element_pixel_box(element, self.width, self.height)
        seed = abs(hash((element.x, element.y, element.z_index))) % 100000
        rng = _SeededRandom(seed)
        image = render_decoration(
            name,
            (box_w, box_h),
            _PaletteView(self.composition.palette),
            rng,
            text=payload.get("text") or "",
        )
        if image is None:
            return None
        rgb, mask = split_rgba(image)
        return Layer(element=element, rgb=rgb, mask=mask)

    # -- frame rendering --------------------------------------------------
    def render_page(self, prepared: PreparedPage, local_t: float) -> Image.Image:
        palette = self.composition.palette
        base = prepared.background
        if base is None:
            base = Image.new("RGB", (self.width, self.height), hex_to_rgba(palette.get("paper", "#F0EBE0"), 255))
        else:
            base = base.copy()

        for layer in prepared.layers:
            element = layer.element
            animation = element.animation or {}
            state = element_state(animation, local_t, prepared.duration, element.opacity)

            out_spec = animation.get("out")
            if out_spec:
                out_state = element_state(out_spec, local_t, prepared.duration, 1.0)
                state.alpha *= out_state.alpha

            if state.alpha <= 0.005:
                continue

            rgb = layer.rgb
            mask = layer.mask

            if layer.zoom_source is not None and animation.get("type") == ZOOM:
                rgb, mask = self._zoom_crop(layer, local_t, prepared.duration)

            if state.visible_lines is not None or state.visible_chars is not None or state.visible_words is not None:
                lines = clip_text(layer.element.payload.get("lines") or [], state)
                text_rgb, text_mask = self._render_text(element, lines, layer.rgb.width)
                rgb, mask = text_rgb, text_mask

            left, top, box_w, box_h = element_pixel_box(element, self.width, self.height)
            x = int(left - layer.pad + state.offset_x * self.width)
            y = int(top - layer.pad + state.offset_y * self.height)

            if state.rotation:
                rgb = rgb.rotate(state.rotation, resample=Image.BICUBIC, expand=True)
                mask = mask.rotate(state.rotation, resample=Image.BICUBIC, expand=True)
                x -= (rgb.width - layer.rgb.width) // 2
                y -= (rgb.height - layer.rgb.height) // 2

            if state.alpha < 0.999:
                mask = mask.point(lambda v: int(v * state.alpha))

            if rgb.mode != "RGB":
                rgb = rgb.convert("RGB")
            base.paste(rgb, (x, y), mask)

        return base

    def _zoom_crop(self, layer: Layer, local_t: float, duration: float) -> tuple[Image.Image, Image.Image]:
        source = layer.zoom_source or layer.rgb
        p = min(1.0, max(0.0, local_t / max(0.1, duration)))
        target_w, target_h = layer.rgb.width, layer.rgb.height
        max_x = max(0, source.width - target_w)
        max_y = max(0, source.height - target_h)
        left = int(max_x * 0.5 * (1 - p))
        top = int(max_y * 0.5 * (1 - p))
        crop = source.crop((left, top, left + target_w, top + target_h))
        mask = layer.mask.crop((0, 0, target_w, target_h))
        return crop, mask

    def page_at(self, time_s: float) -> tuple[PreparedPage, float, PreparedPage | None, float]:
        """Locate the page for a moment in time.

        Returns ``(page, local_t, next_page, transition_duration)``.
        """
        cursor = 0.0
        for index, prepared in enumerate(self.pages):
            if time_s < cursor + prepared.duration or index == len(self.pages) - 1:
                local = max(0.0, time_s - cursor)
                return prepared, local, None, 0.0
            cursor += prepared.duration
        return self.pages[-1], 0.0, None, 0.0

    def frames(self, total_duration: float, transition: float = 0.45) -> Iterator[Image.Image]:
        """Yield every frame of the composition."""
        fps = self.profile.fps
        total_frames = max(1, int(round(total_duration * fps)))
        time_s = 0.0

        # Page start offsets, used for transitions.
        offsets: list[float] = []
        cursor = 0.0
        for prepared in self.pages:
            offsets.append(cursor)
            cursor += prepared.duration

        for index in range(total_frames):
            time_s = index / fps
            page_index = 0
            for i, prepared in enumerate(self.pages):
                if time_s >= offsets[i]:
                    page_index = i
            prepared = self.pages[page_index]
            local = time_s - offsets[page_index]
            frame = self.render_page(prepared, local)

            # Cross-fade into the next page over the last moments of this one.
            if page_index + 1 < len(self.pages):
                td = min(transition, prepared.duration * 0.5)
                if td > 0 and local > prepared.duration - td:
                    weight = transition_weight(
                        self.pages[page_index + 1].page.transition,
                        local - (prepared.duration - td),
                        td,
                    )
                    if weight > 0.001:
                        next_page = self.pages[page_index + 1]
                        next_local = max(0.0, local - (prepared.duration - td))
                        next_frame = self.render_page(next_page, next_local)
                        if weight >= 0.999:
                            frame = next_frame
                        else:
                            frame = Image.blend(frame, next_frame, weight)
            yield frame

    def cleanup(self) -> None:
        for prepared in self.pages:
            if prepared.video is not None:
                prepared.video.cleanup()
        self._text_cache.clear()
        self._photo_cache.clear()
        self._surface_cache.clear()


def _draw_line(draw, font, text: str, x: int, y: int, tracking: float, colour: str) -> None:
    if not text:
        return
    if not tracking:
        draw.text((x, y), text, font=font, fill=colour)
        return
    cursor = float(x)
    for char in text:
        draw.text((cursor, y), char, font=font, fill=colour)
        cursor += font.getlength(char) + tracking


class _SeededRandom:
    """A tiny deterministic random source for drawing helpers."""

    def __init__(self, seed: int) -> None:
        self._state = (seed * 6364136223846793005 + 1442695040888963407) & ((1 << 64) - 1)

    def _next(self) -> float:
        self._state = (self._state * 6364136223846793005 + 1442695040888963407) & ((1 << 64) - 1)
        return ((self._state >> 11) & ((1 << 53) - 1)) / float(1 << 53)

    def random(self) -> float:
        return self._next()

    def uniform(self, a: float, b: float) -> float:
        return a + (b - a) * self._next()

    def randint(self, a: int, b: int) -> int:
        return a + int(self._next() * (b - a + 1))

    def choice(self, seq):
        return seq[int(self._next() * len(seq)) % len(seq)]

    def shuffle(self, seq):
        items = list(seq)
        for i in range(len(items) - 1, 0, -1):
            j = int(self._next() * (i + 1))
            items[i], items[j] = items[j], items[i]
        return items


class _PaletteView:
    """Adapts the plain dict palette to the attribute access the drawings use."""

    def __init__(self, palette: dict[str, Any]) -> None:
        self._palette = palette

    def __getattr__(self, item: str) -> str:
        return self._palette.get(item, "#888888")
