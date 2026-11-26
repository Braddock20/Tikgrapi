"""Procedural decorations.

Everything here is drawn with Pillow at render time: torn paper, tape,
polaroids, stamps, doodles, grain. No image files are required, so the
engine produces a finished look even when the asset library is empty —
and the user's own stickers can be layered on top.
"""
from __future__ import annotations

import math
import random
from typing import Callable

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from video_engine.assets.fonts import FontFace, load_font


# --- helpers -------------------------------------------------------------
def hex_to_rgba(colour: str, alpha: int = 255) -> tuple[int, int, int, int]:
    value = colour.lstrip("#")
    if len(value) == 3:
        value = "".join(c * 2 for c in value)
    if len(value) != 6:
        return (0, 0, 0, alpha)
    try:
        r, g, b = int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)
    except ValueError:
        return (0, 0, 0, alpha)
    return (r, g, b, alpha)


def with_alpha(colour: str, alpha: float) -> tuple[int, int, int, int]:
    r, g, b, _ = hex_to_rgba(colour, 255)
    return (r, g, b, max(0, min(255, int(alpha * 255))))


def drop_shadow(image: Image.Image, *, offset: tuple[int, int] = (6, 10), blur: int = 14, opacity: float = 0.28) -> Image.Image:
    """Return a new RGBA image with a soft shadow behind ``image``."""
    alpha = image.getchannel("A")
    shadow = Image.new("RGBA", image.size, (0, 0, 0, 0))
    shadow_alpha = alpha.point(lambda v: int(v * opacity))
    shadow.paste((0, 0, 0, 255), (0, 0), shadow_alpha)
    shadow = shadow.filter(ImageFilter.GaussianBlur(blur))
    canvas = Image.new("RGBA", (image.width + abs(offset[0]) * 2, image.height + abs(offset[1]) * 2), (0, 0, 0, 0))
    canvas.alpha_composite(shadow, (abs(offset[0]) + offset[0], abs(offset[1]) + offset[1]))
    canvas.alpha_composite(image, (abs(offset[0]), abs(offset[1])))
    return canvas


def noise_layer(size: tuple[int, int], rng: random.Random, strength: int = 14, alpha: int = 255) -> Image.Image:
    """Fine film grain."""
    width, height = size
    seed = rng.randint(0, 2**31 - 1)
    generator = np.random.default_rng(seed)
    data = generator.normal(0, strength, (height, width, 1))
    base = np.full((height, width, 3), 128.0)
    array = np.clip(base + data, 0, 255).astype(np.uint8)
    layer = Image.fromarray(array, "RGB").convert("RGBA")
    layer.putalpha(alpha)
    return layer


def paper_texture(size: tuple[int, int], base: str, rng: random.Random, *, fibres: bool = True) -> Image.Image:
    """A sheet of coloured paper with a subtle grain."""
    width, height = size
    layer = Image.new("RGBA", size, hex_to_rgba(base, 255))
    grain = noise_layer(size, rng, strength=9, alpha=46)
    layer.alpha_composite(grain)

    if fibres:
        lines = Image.new("RGBA", size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(lines)
        count = max(20, (width * height) // 9000)
        for _ in range(count):
            x = rng.uniform(0, width)
            y = rng.uniform(0, height)
            length = rng.uniform(4, 26)
            angle = rng.uniform(0, math.tau)
            tone = rng.choice([(255, 255, 255, 16), (120, 110, 95, 16)])
            draw.line([x, y, x + math.cos(angle) * length, y + math.sin(angle) * length], fill=tone, width=1)
        lines = lines.filter(ImageFilter.GaussianBlur(0.6))
        layer.alpha_composite(lines)

    # A gentle vignette so the page is not perfectly flat.
    vignette = Image.new("L", size, 0)
    vdraw = ImageDraw.Draw(vignette)
    vdraw.ellipse([-width * 0.25, -height * 0.25, width * 1.25, height * 1.25], fill=255)
    vignette = vignette.filter(ImageFilter.GaussianBlur(min(width, height) * 0.12))
    shade = Image.new("RGBA", size, hex_to_rgba("#6b5a45", 255))
    shade.putalpha(Image.eval(vignette, lambda v: int((255 - v) * 0.16)))
    layer.alpha_composite(shade)
    return layer


def torn_edge_mask(size: tuple[int, int], rng: random.Random, amplitude: int = 18) -> Image.Image:
    """A mask with a ragged, torn-paper border."""
    width, height = size
    mask = Image.new("L", size, 255)
    draw = ImageDraw.Draw(mask)
    step = max(6, width // 28)
    for x in range(0, width + step, step):
        jitter = rng.randint(-amplitude, amplitude)
        draw.rectangle([x, 0, x + step, max(0, jitter)], fill=0)
        draw.rectangle([x, height - max(0, jitter), x + step, height], fill=0)
    for y in range(0, height + step, step):
        jitter = rng.randint(-amplitude, amplitude)
        draw.rectangle([0, y, max(0, jitter), y + step], fill=0)
        draw.rectangle([width - max(0, jitter), y, width, y + step], fill=0)
    return mask.filter(ImageFilter.GaussianBlur(0.8))


# --- individual decorations ---------------------------------------------
def washi_tape(size: tuple[int, int], colour: str, rng: random.Random) -> Image.Image:
    """A translucent strip of tape with torn ends."""
    width, height = size
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    alpha = rng.randint(150, 205)
    draw.rectangle([0, 0, width - 1, height - 1], fill=with_alpha(colour, alpha / 255))
    # Stripes or dots for a bit of character.
    if rng.random() < 0.5:
        stripe = with_alpha("#FFFFFF", 0.28)
        for x in range(-height, width, max(12, height // 2)):
            draw.line([x, height, x + height, 0], fill=stripe, width=max(2, height // 6))
    else:
        dot = with_alpha("#FFFFFF", 0.24)
        for x in range(6, width, max(10, height // 2)):
            for y in range(height // 4, height, max(10, height // 2)):
                draw.ellipse([x, y, x + 3, y + 3], fill=dot)
    # Torn ends.
    mask = Image.new("L", size, 255)
    mdraw = ImageDraw.Draw(mask)
    for side in (0, 1):
        x = 0 if side == 0 else width - height // 3
        for i in range(height // 3):
            mdraw.polygon(
                [
                    (x + rng.randint(0, height // 5), i),
                    (x + (height // 3 if side else 0) + rng.randint(0, height // 6), i),
                    (x + (height // 3 if side else 0) + rng.randint(0, height // 6), i + 1),
                    (x + rng.randint(0, height // 5), i + 1),
                ],
                fill=255 if side == 0 else 0,
            )
    layer.putalpha(Image.composite(layer.getchannel("A"), Image.new("L", size, 0), mask))
    return layer


def polaroid(photo: Image.Image, palette, caption: str = "") -> Image.Image:
    """A polaroid frame with a photo and an optional handwritten caption."""
    width, height = photo.size
    bottom = max(56, int(height * 0.26))
    frame = Image.new("RGBA", (width, height + bottom), hex_to_rgba(palette.surface, 255))
    inset = int(width * 0.06)
    frame.paste(photo.convert("RGBA"), (inset, inset))
    if caption:
        try:
            font = load_font(str(_default_handwritten()), max(20, int(width * 0.055)))
        except Exception:  # noqa: BLE001
            font = load_font("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 20)
        draw = ImageDraw.Draw(frame)
        draw.text((inset + 4, height + bottom - int(bottom * 0.78)), caption, font=font, fill=hex_to_rgba(palette.ink, 230))
    return frame


def _default_handwritten() -> FontFace:
    from video_engine.assets.fonts import FontRegistry
    from video_engine.config import settings

    registry = FontRegistry(settings.assets_dir / "fonts")
    faces = registry.category("handwritten")
    if faces:
        return faces[0]
    return FontFace(path=__import__("pathlib").Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"), category="sans")


def star(size: tuple[int, int], colour: str, rng: random.Random) -> Image.Image:
    width, height = size
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    points = rng.choice([4, 5, 5, 6, 8])
    cx, cy = width / 2, height / 2
    outer = min(width, height) * 0.48
    inner = outer * rng.uniform(0.32, 0.52)
    vertices: list[tuple[float, float]] = []
    for index in range(points * 2):
        radius = outer if index % 2 == 0 else inner
        angle = math.pi / 2 + index * math.pi / points - rng.uniform(0, 0.12)
        vertices.append((cx + math.cos(angle) * radius, cy + math.sin(angle) * radius))
    draw.polygon(vertices, fill=hex_to_rgba(colour, 245))
    return layer


def flower(size: tuple[int, int], colour: str, rng: random.Random) -> Image.Image:
    width, height = size
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    cx, cy = width / 2, height / 2
    petals = rng.choice([5, 6, 8])
    length = min(width, height) * 0.42
    for index in range(petals):
        angle = index * math.tau / petals + rng.uniform(-0.1, 0.1)
        px = cx + math.cos(angle) * length * 0.55
        py = cy + math.sin(angle) * length * 0.55
        draw.ellipse(
            [px - length * 0.34, py - length * 0.24, px + length * 0.34, py + length * 0.24],
            fill=with_alpha(colour, 0.88),
        )
    draw.ellipse([cx - length * 0.2, cy - length * 0.2, cx + length * 0.2, cy + length * 0.2], fill=hex_to_rgba("#F3D06A", 245))
    return layer


def heart(size: tuple[int, int], colour: str) -> Image.Image:
    width, height = size
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    draw.ellipse([0, 0, width * 0.55, height * 0.55], fill=hex_to_rgba(colour, 240))
    draw.ellipse([width * 0.45, 0, width, height * 0.55], fill=hex_to_rgba(colour, 240))
    draw.polygon([(width * 0.04, height * 0.42), (width * 0.96, height * 0.42), (width * 0.5, height)], fill=hex_to_rgba(colour, 240))
    return layer


def doodle_arrow(size: tuple[int, int], colour: str, rng: random.Random) -> Image.Image:
    width, height = size
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    points = [(width * 0.05, height * 0.7), (width * 0.4, height * 0.55), (width * 0.78, height * 0.2)]
    draw.line(points, fill=hex_to_rgba(colour, 235), width=max(3, int(height * 0.06)), joint="curve")
    head = 0.22
    draw.line(
        [(width * 0.78, height * 0.2), (width * (0.78 - head), height * 0.22)],
        fill=hex_to_rgba(colour, 235),
        width=max(3, int(height * 0.06)),
    )
    draw.line(
        [(width * 0.78, height * 0.2), (width * 0.72, height * (0.2 + head))],
        fill=hex_to_rgba(colour, 235),
        width=max(3, int(height * 0.06)),
    )
    return layer


def scribble_underline(size: tuple[int, int], colour: str, rng: random.Random) -> Image.Image:
    width, height = size
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    points = []
    steps = 24
    for index in range(steps + 1):
        x = width * index / steps
        y = height * 0.5 + math.sin(index * 0.8 + rng.random()) * height * 0.22
        points.append((x, y))
    draw.line(points, fill=hex_to_rgba(colour, 230), width=max(3, int(height * 0.16)), joint="curve")
    return layer


def highlight_mark(size: tuple[int, int], colour: str, rng: random.Random) -> Image.Image:
    width, height = size
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    x0 = width * 0.03
    x1 = width * 0.97
    y0 = height * rng.uniform(0.25, 0.4)
    y1 = height * rng.uniform(0.6, 0.78)
    draw.polygon([(x0, y0), (x1, y0 - height * 0.06), (x1, y1), (x0, y1 + height * 0.05)], fill=with_alpha(colour, 0.35))
    return layer


def date_stamp(size: tuple[int, int], text: str, colour: str, rng: random.Random, face: FontFace | None = None) -> Image.Image:
    width, height = size
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    border = hex_to_rgba(colour, 190)
    inset = 3
    draw.ellipse([inset, inset, width - inset, height - inset], outline=border, width=max(2, int(height * 0.035)))
    draw.ellipse(
        [inset * 3, inset * 3, width - inset * 3, height - inset * 3],
        outline=border,
        width=max(1, int(height * 0.015)),
    )
    if face is not None:
        font = load_font(str(face.path), int(height * 0.22), 700)
    else:
        font = load_font("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", int(height * 0.22))
    bbox = draw.textbbox((0, 0), text, font=font)
    draw.text(
        ((width - (bbox[2] - bbox[0])) / 2, (height - (bbox[3] - bbox[1])) / 2 - bbox[1]),
        text,
        font=font,
        fill=border,
    )
    return layer


def paperclip(size: tuple[int, int], colour: str) -> Image.Image:
    """A bent wire clip drawn as two nested loops."""
    width, height = size
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    stroke = max(2, int(min(width, height) * 0.09))
    inset = stroke

    # Outer loop: a tall rounded rectangle.
    draw.rounded_rectangle(
        [inset, inset * 1.4, width - inset, height - inset * 1.4],
        radius=int((width - inset * 2) * 0.46),
        outline=hex_to_rgba(colour, 230),
        width=stroke,
    )
    # Inner loop, offset upwards so the wire reads as bent.
    inner = inset * 3.0
    draw.rounded_rectangle(
        [inner, inner * 0.9, width - inner, height - inner * 1.7],
        radius=int((width - inner * 2) * 0.46),
        outline=hex_to_rgba(colour, 200),
        width=max(1, stroke - 1),
    )
    # The little catch at the top.
    cap = stroke * 1.6
    draw.arc(
        [width * 0.5 - cap, inset * 0.2, width * 0.5 + cap, inset * 0.2 + cap * 2],
        start=200,
        end=340,
        fill=hex_to_rgba(colour, 230),
        width=stroke,
    )
    return layer


def corner_fold(size: tuple[int, int], colour: str) -> Image.Image:
    width, height = size
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    fold = int(min(width, height) * 0.42)
    draw.polygon([(width - fold, 0), (width, 0), (width, fold)], fill=with_alpha(colour, 0.85))
    draw.line([(width - fold, 0), (width, fold)], fill=hex_to_rgba("#000000", 40), width=2)
    return layer


def envelope(size: tuple[int, int], palette) -> Image.Image:
    width, height = size
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    body = hex_to_rgba(palette.surface, 255)
    draw.rectangle([0, 0, width - 1, height - 1], fill=body, outline=hex_to_rgba(palette.frame, 255), width=3)
    draw.polygon([(0, 0), (width / 2, height * 0.58), (width, 0)], fill=hex_to_rgba(palette.frame, 255))
    draw.line([(0, 0), (width / 2, height * 0.58), (width, 0)], fill=hex_to_rgba(palette.ink, 60), width=3)
    return layer


def film_border(size: tuple[int, int], colour: str) -> Image.Image:
    """The sprocket strip of 35mm film."""
    width, height = size
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    strip = max(10, int(width * 0.07))
    draw.rectangle([0, 0, width - 1, height - 1], fill=hex_to_rgba(colour, 240))
    hole = max(5, int(strip * 0.55))
    step = hole * 3
    for x in range(int(strip * 0.2), width - hole, step):
        for y in (int(strip * 0.25), height - int(strip * 0.25) - hole):
            draw.rounded_rectangle([x, y, x + hole, y + hole], radius=2, fill=(0, 0, 0, 0))
    inner_x0 = strip
    inner_x1 = width - strip
    inner_y = int(strip * 1.2)
    draw.rectangle([inner_x0, inner_y, inner_x1, height - inner_y], fill=(0, 0, 0, 0))
    return layer


def sticker_label(size: tuple[int, int], text: str, colour: str, rng: random.Random, face: FontFace | None = None) -> Image.Image:
    width, height = size
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    draw.rounded_rectangle([2, 2, width - 3, height - 3], radius=int(height * 0.3), fill=hex_to_rgba(colour, 245))
    font = load_font(str(face.path), int(height * 0.5), 700) if face else load_font(
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", int(height * 0.5)
    )
    bbox = draw.textbbox((0, 0), text, font=font)
    draw.text(
        ((width - (bbox[2] - bbox[0])) / 2, (height - (bbox[3] - bbox[1])) / 2 - bbox[1]),
        text,
        font=font,
        fill=hex_to_rgba("#FFFFFF", 245),
    )
    return layer


def splat(size: tuple[int, int], colour: str, rng: random.Random) -> Image.Image:
    width, height = size
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    cx, cy = width / 2, height / 2
    radius = min(width, height) * 0.42
    # A soft blot: smooth radii, low alpha, a few satellite drops.
    points = []
    for index in range(24):
        angle = index * math.tau / 24
        r = radius * (0.82 + 0.18 * math.sin(index * 1.7 + rng.random()))
        points.append((cx + math.cos(angle) * r, cy + math.sin(angle) * r * 0.86))
    draw.polygon(points, fill=with_alpha(colour, 0.5))
    layer = layer.filter(ImageFilter.GaussianBlur(max(1.0, min(width, height) * 0.03)))
    for _ in range(rng.randint(2, 4)):
        angle = rng.uniform(0, math.tau)
        distance = radius * rng.uniform(1.05, 1.45)
        r = radius * rng.uniform(0.05, 0.12)
        x = cx + math.cos(angle) * distance
        y = cy + math.sin(angle) * distance
        draw.ellipse([x - r, y - r, x + r, y + r], fill=with_alpha(colour, 0.45))
    return layer


def moon(size: tuple[int, int], colour: str, rng: random.Random | None = None) -> Image.Image:
    width, height = size
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    draw.ellipse([0, 0, width, height], fill=hex_to_rgba(colour, 245))
    draw.ellipse([width * 0.32, -height * 0.12, width * 1.3, height * 0.92], fill=(0, 0, 0, 0))
    return layer


def sunburst(size: tuple[int, int], colour: str, rng: random.Random) -> Image.Image:
    width, height = size
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    cx, cy = width / 2, height / 2
    rays = rng.choice([8, 12, 16])
    for index in range(rays):
        angle = index * math.tau / rays
        length = min(width, height) * 0.48
        x = cx + math.cos(angle) * length
        y = cy + math.sin(angle) * length
        draw.line([cx, cy, x, y], fill=with_alpha(colour, 0.55), width=max(2, int(min(width, height) * 0.03)))
    draw.ellipse([cx - width * 0.12, cy - height * 0.12, cx + width * 0.12, cy + height * 0.12], fill=with_alpha(colour, 0.8))
    return layer


def wave_rule(size: tuple[int, int], colour: str, rng: random.Random) -> Image.Image:
    width, height = size
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    points = []
    amplitude = height * 0.3
    for index in range(0, width + 6, 6):
        y = height / 2 + math.sin(index / max(8, width / 12) * math.tau) * amplitude
        points.append((index, y))
    draw.line(points, fill=hex_to_rgba(colour, 200), width=max(2, int(height * 0.18)), joint="curve")
    return layer


DECORATIONS: dict[str, Callable[..., Image.Image]] = {
    "star": star,
    "flower": flower,
    "heart": heart,
    "doodle_arrow": doodle_arrow,
    "scribble": scribble_underline,
    "highlight": highlight_mark,
    "date_stamp": date_stamp,
    "paperclip": paperclip,
    "corner_fold": corner_fold,
    "envelope": envelope,
    "film_border": film_border,
    "sticker": sticker_label,
    "splat": splat,
    "moon": moon,
    "sunburst": sunburst,
    "wave": wave_rule,
}


def render_decoration(
    name: str,
    size: tuple[int, int],
    palette,
    rng: random.Random,
    *,
    text: str = "",
    face: FontFace | None = None,
) -> Image.Image | None:
    """Render one named decoration at the requested pixel size."""
    fn = DECORATIONS.get(name)
    if fn is None:
        return None
    width = max(8, int(size[0]))
    height = max(8, int(size[1]))
    if fn in (date_stamp, sticker_label):
        return fn((width, height), text or "2024", palette.accent if fn is date_stamp else palette.accent2, rng, face)
    if fn is envelope:
        return fn((width, height), palette)
    if fn is film_border:
        return fn((width, height), palette.ink)
    if fn is corner_fold:
        return fn((width, height), palette.frame)
    if fn in (star, flower, doodle_arrow, scribble_underline, highlight_mark, splat, sunburst, wave_rule):
        colour = rng.choice([palette.accent, palette.accent2, palette.ink_soft])
        return fn((width, height), colour, rng)
    if fn is heart:
        colour = rng.choice([palette.accent, palette.accent2, palette.ink_soft])
        return fn((width, height), colour)
    if fn is moon:
        colour = rng.choice([palette.accent, palette.accent2, palette.ink_soft])
        return fn((width, height), colour, rng)
    if fn is paperclip:
        return fn((width, height), palette.ink_soft)
    return None
