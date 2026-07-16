"""Render deterministic raster brand assets without external services."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "web" / "og-image.png"


def font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    candidates = [
        Path("C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default(size=size)


def interpolate(start: tuple[int, int, int], end: tuple[int, int, int], ratio: float) -> tuple[int, int, int]:
    return tuple(round(left + (right - left) * ratio) for left, right in zip(start, end, strict=True))


def draw_gradient(image: Image.Image) -> None:
    pixels = image.load()
    for y in range(image.height):
        for x in range(image.width):
            vertical = y / image.height
            base = interpolate((8, 16, 24), (11, 21, 30), vertical)
            glow_distance = ((x - 960) ** 2 / 520**2 + (y - 85) ** 2 / 380**2) ** 0.5
            glow = max(0.0, 1.0 - glow_distance) * 0.42
            pixels[x, y] = interpolate(base, (64, 49, 135), glow) + (255,)


def draw_mark(draw: ImageDraw.ImageDraw, origin: tuple[int, int], scale: float) -> None:
    ox, oy = origin
    white_path = [(22, 49), (40, 17), (58, 45)]
    accent_path = [(22, 49), (44, 49), (61, 78), (84, 42)]

    def points(path: list[tuple[int, int]]) -> list[tuple[int, int]]:
        return [(round(ox + x * scale), round(oy + y * scale)) for x, y in path]

    width = round(8 * scale)
    draw.line(points(white_path), fill=(247, 248, 251), width=width, joint="curve")
    draw.line(points(accent_path), fill=(114, 92, 255), width=round(9 * scale), joint="curve")
    for x, y, color in [
        (40, 17, (247, 248, 251)), (22, 49, (247, 248, 251)),
        (61, 78, (114, 92, 255)), (84, 42, (78, 58, 242)),
    ]:
        cx, cy = ox + x * scale, oy + y * scale
        radius = 7 * scale
        draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=(11, 20, 30), outline=color, width=round(5 * scale))


def render() -> None:
    image = Image.new("RGBA", (1200, 630), (8, 16, 24, 255))
    draw_gradient(image)
    draw = ImageDraw.Draw(image)

    # Brand network pattern.
    routes = [
        [(45, 100), (230, 44), (390, 136), (565, 78), (754, 152), (955, 70), (1160, 126)],
        [(18, 520), (206, 430), (405, 498), (597, 405), (810, 486), (1035, 397), (1218, 452)],
    ]
    for index, route in enumerate(routes):
        color = (128, 109, 255, 55) if index == 0 else (64, 217, 160, 38)
        draw.line(route, fill=color, width=2, joint="curve")
        for x, y in route[1:-1]:
            draw.ellipse((x - 5, y - 5, x + 5, y + 5), fill=(10, 19, 28, 255), outline=color, width=2)

    # Logo tile.
    draw.rounded_rectangle((72, 102, 322, 352), radius=50, fill=(14, 24, 36, 245), outline=(128, 109, 255, 82), width=2)
    draw_mark(draw, (72, 102), 2.5)

    title_font = font(72, bold=True)
    subtitle_font = font(31)
    small_font = font(22, bold=True)
    label_font = font(18)
    draw.text((372, 105), "All As Planned", font=title_font, fill=(247, 249, 252))
    draw.text((376, 207), "Контент от идеи до публикации", font=subtitle_font, fill=(185, 195, 209))
    draw.text((376, 248), "в одной системе", font=subtitle_font, fill=(167, 157, 255))

    badge = (376, 318, 786, 366)
    draw.rounded_rectangle(badge, radius=24, fill=(128, 109, 255, 28), outline=(128, 109, 255, 76), width=2)
    draw.ellipse((394, 336, 404, 346), fill=(64, 217, 160))
    draw.text((420, 329), "РАБОЧЕЕ ПРОСТРАНСТВО МАРКЕТОЛОГА", font=label_font, fill=(210, 216, 227))

    features = ["Контент-план", "Медиатека", "Согласования", "Видео", "AI-студия"]
    cursor_x = 74
    for feature in features:
        width = round(draw.textlength(feature, font=small_font)) + 44
        draw.rounded_rectangle((cursor_x, 515, cursor_x + width, 561), radius=14, fill=(19, 31, 44, 230), outline=(61, 78, 98, 200), width=1)
        draw.text((cursor_x + 22, 524), feature, font=small_font, fill=(205, 213, 224))
        cursor_x += width + 12

    draw.text((984, 577), "allasplanned.ru", font=font(19, bold=True), fill=(129, 144, 161))
    image.convert("RGB").save(OUTPUT, format="PNG", optimize=True, quality=92)
    print(f"Rendered {OUTPUT} ({OUTPUT.stat().st_size} bytes)")


if __name__ == "__main__":
    render()
