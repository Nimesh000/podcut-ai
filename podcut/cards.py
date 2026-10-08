"""Title cards, lower-third name tag and thumbnail, drawn with Pillow."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .assets import fonts


def _font(weight: str, size: int) -> ImageFont.FreeTypeFont:
    path = fonts().get(weight) or ""
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default(size=size)


def _hex(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    return tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_w: int, max_lines: int = 3) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        test = f"{cur} {w}".strip()
        if draw.textlength(test, font=font) <= max_w:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip(".,") + "..."
    return lines


def _background(w: int, h: int, bg: tuple, accent: tuple) -> Image.Image:
    img = Image.new("RGB", (w, h), bg)
    glow = Image.new("RGB", (w, h), bg)
    gd = ImageDraw.Draw(glow)
    r = int(h * 0.55)
    gd.ellipse((int(w * 0.72) - r, int(h * 0.2) - r, int(w * 0.72) + r, int(h * 0.2) + r),
               fill=tuple(int(bg[i] * 0.6 + accent[i] * 0.4) for i in range(3)))
    glow = glow.filter(ImageFilter.GaussianBlur(h // 4))
    return Image.blend(img, glow, 0.55)


def title_card(path: Path, w: int, h: int, title: str, kicker: str, subtitle: str, cfg: dict,
               logo: Path | None = None) -> None:
    bg, accent = _hex(cfg["background_color"]), _hex(cfg["accent_color"])
    img = _background(w, h, bg, accent)
    d = ImageDraw.Draw(img)
    s = h / 1080
    x = int(140 * s)
    title_font = _font("Bold", int(92 * s))
    lines = _wrap(d, title or "Podcast Highlights", title_font, int(w * 0.80), 3)
    line_h = int(110 * s)
    block_h = line_h * len(lines) + int(160 * s)
    y = (h - block_h) // 2

    if kicker:
        d.text((x, y), kicker.upper(), font=_font("SemiBold", int(34 * s)), fill=accent)
    y += int(70 * s)
    for line in lines:
        d.text((x, y), line, font=title_font, fill=(255, 255, 255))
        y += line_h
    y += int(18 * s)
    d.rectangle((x, y, x + int(120 * s), y + int(8 * s)), fill=accent)
    if subtitle:
        d.text((x, y + int(34 * s)), subtitle, font=_font("Regular", int(42 * s)), fill=(210, 214, 222))
    if logo and logo.exists():
        _paste_logo(img, logo, int(170 * s), corner=(w - int(80 * s), int(70 * s)))
    img.save(path)


def outro_card(path: Path, w: int, h: int, text: str, subtext: str, cfg: dict, logo: Path | None = None) -> None:
    bg, accent = _hex(cfg["background_color"]), _hex(cfg["accent_color"])
    img = _background(w, h, bg, accent)
    d = ImageDraw.Draw(img)
    s = h / 1080
    big, small = _font("Bold", int(96 * s)), _font("Regular", int(44 * s))
    tw = d.textlength(text, font=big)
    d.text(((w - tw) / 2, h * 0.40), text, font=big, fill=(255, 255, 255))
    sw = d.textlength(subtext, font=small)
    d.text(((w - sw) / 2, h * 0.40 + 175 * s), subtext, font=small, fill=(210, 214, 222))
    bw = int(120 * s)
    d.rectangle(((w - bw) / 2, h * 0.40 + 145 * s, (w + bw) / 2, h * 0.40 + 153 * s), fill=accent)
    if logo and logo.exists():
        _paste_logo(img, logo, int(200 * s), corner=None, center=(w // 2, int(h * 0.24)))
    img.save(path)


def lower_third(path: Path, w: int, h: int, name: str, role: str, cfg: dict) -> None:
    """Transparent PNG the size of the frame with a name tag bottom-left."""
    accent = _hex(cfg["accent_color"])
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = h / 1080
    name_f, role_f = _font("Bold", int(46 * s)), _font("Regular", int(32 * s))
    pad = int(26 * s)
    box_w = int(max(d.textlength(name, font=name_f), d.textlength(role or "", font=role_f)) + pad * 2 + 14 * s)
    box_h = int((118 if role else 80) * s)
    x0, y0 = int(90 * s), int(h - 300 * s)
    d.rounded_rectangle((x0, y0, x0 + box_w, y0 + box_h), radius=int(10 * s), fill=(14, 17, 22, 215))
    d.rectangle((x0, y0, x0 + int(10 * s), y0 + box_h), fill=accent + (255,))
    d.text((x0 + pad + 10 * s, y0 + int(14 * s)), name, font=name_f, fill=(255, 255, 255, 255))
    if role:
        d.text((x0 + pad + 10 * s, y0 + int(68 * s)), role, font=role_f, fill=(215, 218, 225, 255))
    img.save(path)


def thumbnail(frame: Path, path: Path, title: str, cfg: dict) -> None:
    img = Image.open(frame).convert("RGB").resize((1280, 720))
    shade = Image.new("RGBA", img.size, (0, 0, 0, 0))
    sd = ImageDraw.Draw(shade)
    for i in range(360):
        alpha = int(200 * (i / 360) ** 1.4)
        sd.line((0, 360 + i, 1280, 360 + i), fill=(0, 0, 0, alpha))
    img = Image.alpha_composite(img.convert("RGBA"), shade).convert("RGB")
    d = ImageDraw.Draw(img)
    font = _font("Bold", 64)
    lines = _wrap(d, title, font, 1140, 2)
    y = 720 - 60 - 76 * len(lines)
    accent = _hex(cfg["accent_color"])
    d.rectangle((60, y - 26, 160, y - 18), fill=accent)
    for line in lines:
        d.text((60, y), line, font=font, fill=(255, 255, 255), stroke_width=2, stroke_fill=(0, 0, 0))
        y += 76
    img.save(path, quality=92)


def _paste_logo(img: Image.Image, logo: Path, size: int, corner=None, center=None) -> None:
    try:
        lg = Image.open(logo).convert("RGBA")
    except OSError:
        return
    lg.thumbnail((size, size))
    if corner:
        pos = (corner[0] - lg.width, corner[1])
    else:
        pos = (center[0] - lg.width // 2, center[1] - lg.height // 2)
    img.paste(lg, pos, lg)
