"""Рисует карточку игрока картинкой (в стиле примера: тёмная карточка, аватар с кольцом, плашки справа).

Шрифт: положите файлы fonts/font.ttf и fonts/font-bold.ttf (например Montserrat) — иначе возьмётся
DejaVu/Arial из системы. Нужна поддержка кириллицы.
"""
import io
import os
import re
from datetime import datetime, timezone
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont

BASE = os.path.dirname(os.path.abspath(__file__))
W, H = 1000, 416

BG = (30, 31, 34, 255)
PANEL = (43, 45, 49, 255)
OUTLINE = (58, 60, 66, 255)
TXT = (242, 243, 245, 255)
MUTED = (148, 155, 164, 255)
SOFT = (205, 209, 215, 255)
BLUE = (88, 101, 242, 255)
PURPLE = (155, 89, 182, 255)
YELLOW = (254, 231, 92, 255)
ACCENT = {
    "clean": (87, 242, 135, 255),
    "warn": (254, 231, 92, 255),
    "rep": (230, 126, 34, 255),
    "bl": (237, 66, 69, 255),
}

_REG = [
    os.path.join(BASE, "fonts", "font.ttf"),
    os.path.join(BASE, "fonts", "Montserrat-Regular.ttf"),
    "DejaVuSans.ttf",
    "arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
]
_BOLD = [
    os.path.join(BASE, "fonts", "font-bold.ttf"),
    os.path.join(BASE, "fonts", "Montserrat-Bold.ttf"),
    "DejaVuSans-Bold.ttf",
    "arialbd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
]


@lru_cache(maxsize=64)
def _f(size, bold=False):
    for path in _BOLD if bold else _REG:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size)
    except TypeError:
        return ImageFont.load_default()


def _fit(dr, text, size, max_w, bold=False, min_size=14):
    """Подбирает размер шрифта, чтобы текст влез; если не влезает совсем — обрезает с «…»."""
    text = text or "—"
    s = size
    while s > min_size and dr.textlength(text, font=_f(s, bold)) > max_w:
        s -= 2
    f = _f(max(s, min_size), bold)
    if dr.textlength(text, font=f) <= max_w:
        return f, text
    while len(text) > 1 and dr.textlength(text + "…", font=f) > max_w:
        text = text[:-1]
    return f, text + "…"


def _paste_circle(img, xy, size, fill=None, image=None):
    """Круг со сглаживанием: заливка цветом или картинка (аватар)."""
    k = 4
    mask = Image.new("L", (size * k, size * k), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size * k - 1, size * k - 1), fill=255)
    mask = mask.resize((size, size), Image.Resampling.LANCZOS)
    if image is not None:
        layer = image.resize((size, size), Image.Resampling.LANCZOS).convert("RGBA")
    else:
        layer = Image.new("RGBA", (size, size), fill)
    img.paste(layer, xy, mask)


def _block(dr, x, y, w, h, title, value, color):
    dr.rounded_rectangle((x, y, x + w, y + h), 18, fill=PANEL)
    dr.rounded_rectangle((x, y + 20, x + 5, y + h - 20), 3, fill=color)
    dr.text((x + 22, y + 16), title, font=_f(18, True), fill=TXT)
    lines = (value or "—").split("\n")[:2]
    top = y + 52 if len(lines) == 1 else y + 48
    for i, line in enumerate(lines):
        f, t = _fit(dr, line, 24 if len(lines) == 1 else 20, w - 32)
        dr.text((x + 22, top + i * 28), t, font=f, fill=SOFT)


def _bar(dr, x, y, w, h, frac, color, label, value, total):
    dr.text((x, y - 28), label, font=_f(19, True), fill=MUTED)
    dr.rounded_rectangle((x, y, x + w, y + h), 14, fill=PANEL)
    if frac > 0:
        fw = max(h, int(w * min(frac, 1)))
        dr.rounded_rectangle((x, y, x + fw, y + h), 14, fill=color)
        dark = tuple(int(c * 0.55) for c in color[:3]) + (255,)
        dr.rounded_rectangle((x + 3, y + 3, x + fw - 3, y + h - 3), 12, fill=dark)
    dr.text((x + 18, y + 6), str(value), font=_f(34, True), fill=TXT)
    dr.text((x + 62, y + 17), f"из {total}", font=_f(19), fill=SOFT)


def _short(url):
    return re.sub(r"^https?://(www\.)?", "", url or "").rstrip("/") or "—"


def _chip(dr, x, y, text, size=17):
    f = _f(size)
    w = int(dr.textlength(text, font=f)) + 28
    dr.rounded_rectangle((x, y, x + w, y + 30), 15, fill=PANEL)
    dr.text((x + 14, y + 5), text, font=f, fill=SOFT)
    return w


def render_card(d: dict, avatar: bytes | None = None) -> bytes:
    """d: name, handle, steam_id, steam_url, union_url, created (str), gang, full (bool),
    status (clean/warn/rep/bl), warns, reps, bls, position (str), max_warns, max_reps."""
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    dr = ImageDraw.Draw(img)
    dr.rounded_rectangle((0, 0, W - 1, H - 1), 30, fill=BG, outline=OUTLINE, width=2)

    # аватар с кольцом статуса
    # публичная карточка не раскрывает статус наказаний — нейтральное кольцо
    _paste_circle(img, (34, 34), 156, fill=ACCENT[d["status"]] if d["full"] else BLUE)
    av = None
    if avatar:
        try:
            av = Image.open(io.BytesIO(avatar)).convert("RGBA")
        except Exception:
            av = None
    if av is not None:
        _paste_circle(img, (42, 42), 140, image=av)
    else:
        _paste_circle(img, (42, 42), 140, fill=(70, 73, 80, 255))
    dr = ImageDraw.Draw(img)

    # имя, @discord, чипы
    f, t = _fit(dr, d["name"], 40, 310, bold=True, min_size=22)
    dr.text((214, 46), t, font=f, fill=TXT)
    f, t = _fit(dr, d["handle"], 22, 290)
    dr.text((214, 98), t, font=f, fill=MUTED)
    _chip(dr, 214, 134, f"Карточка с {d['created']}")
    if d.get("gang"):
        f, t = _fit(dr, f"Банда: {d['gang']}", 17, 290, min_size=13)
        w = int(dr.textlength(t, font=f)) + 28
        dr.rounded_rectangle((214, 168, 214 + w, 198), 15, fill=PANEL)
        dr.text((228, 173), t, font=f, fill=SOFT)

    # правая сетка
    x0, cw, gap = 548, 206, 12
    rows = [34, 154, 274]
    if d["full"]:
        bls = d["bls"]
        _block(dr, x0, rows[0], cw, 108, "STEAM ID", d["steam_id"] or "—", BLUE)
        _block(dr, x0 + cw + gap, rows[0], cw, 108, "ЧС",
               f"Активных: {bls}" if bls else "Нет", ACCENT["bl"] if bls else ACCENT["clean"])
        _block(dr, x0, rows[1], cw, 108, "STEAM", _short(d["steam_url"]), BLUE)
        _block(dr, x0 + cw + gap, rows[1], cw, 108, "UNIONTEAM", _short(d["union_url"]), PURPLE)
        _block(dr, x0, rows[2], cw * 2 + gap, 108, "ДОЛЖНОСТЬ", d["position"], YELLOW)
        # полосы счётчиков (как XP-полосы в примере)
        _bar(dr, 34, 238, 486, 52, d["warns"] / d["max_warns"], ACCENT["warn"],
             "ПРЕДУПРЕЖДЕНИЯ", d["warns"], d["max_warns"])
        _bar(dr, 34, 328, 486, 52, d["reps"] / d["max_reps"], ACCENT["rep"],
             "ВЫГОВОРЫ", d["reps"], d["max_reps"])
    else:
        _block(dr, x0, rows[0], cw, 108, "НИК", d["name"], BLUE)
        _block(dr, x0 + cw + gap, rows[0], cw, 108, "STEAM ID", d["steam_id"] or "—", BLUE)
        _block(dr, x0, rows[1], cw, 108, "STEAM", _short(d["steam_url"]), BLUE)
        _block(dr, x0 + cw + gap, rows[1], cw, 108, "UNIONTEAM", _short(d["union_url"]), PURPLE)
        _block(dr, x0, rows[2], cw * 2 + gap, 108, "DISCORD", d["handle"], MUTED)
        dr.rounded_rectangle((34, 214, 520, 382), 18, fill=PANEL)
        dr.text((58, 238), "Публичная карточка", font=_f(24, True), fill=TXT)
        dr.text((58, 282), "Выговоры, ЧС и должность видны", font=_f(19), fill=MUTED)
        dr.text((58, 310), "только персоналу сервера.", font=_f(19), fill=MUTED)

    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def fmt_date(ts: int) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%d/%m/%Y")
