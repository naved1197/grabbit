"""Builds the app and tray icons from the website's icon (run once; output is committed).

    uv run python packaging/make_icons.py
"""

from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
OUT = HERE / "icons"
SOURCE = HERE.parent.parent / "grabbit-web" / "app" / "static" / "icon-512.png"


def tray_glyph(px: int) -> Image.Image:
    """The Grabbit icon as a one-colour shape with the bunny and arrow cut out.

    macOS draws it as a "template" image, tinted to match the menu bar.
    Coordinates follow grabbit-web/app/static/favicon.svg (a 64x64 box).
    """
    s = 8  # draw big, then shrink, for smooth edges
    size = 64 * s
    mask = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(mask)
    d.rounded_rectangle((2 * s, 2 * s, 62 * s, 62 * s), radius=16 * s, fill=255)
    for x in (17, 37):  # ears
        d.rounded_rectangle((x * s, 8 * s, (x + 10) * s, 26 * s), radius=5 * s, fill=0,
                            corners=(True, True, False, False))
    w = 5 * s
    for (x1, y1), (x2, y2) in [((32, 24), (32, 44)), ((32, 44), (24, 36)), ((32, 44), (40, 36)), ((19, 52), (45, 52))]:
        d.line((x1 * s, y1 * s, x2 * s, y2 * s), fill=0, width=w)
        for x, y in ((x1, y1), (x2, y2)):  # round caps
            d.ellipse((x * s - w / 2, y * s - w / 2, x * s + w / 2, y * s + w / 2), fill=0)
    mask = mask.resize((px, px), Image.LANCZOS)
    glyph = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    glyph.putalpha(mask)
    return glyph


def main() -> None:
    OUT.mkdir(exist_ok=True)
    src = Image.open(SOURCE).convert("RGBA")
    src.save(OUT / "Grabbit.icns")
    src.save(OUT / "Grabbit.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    tray_glyph(36).save(OUT / "tray-mac.png")      # 18pt at 2x
    src.resize((64, 64), Image.LANCZOS).save(OUT / "tray.png")  # Windows tray (full colour)
    tray_glyph(256).save(OUT / "tray-mac-preview.png")
    print("icons written to", OUT)


if __name__ == "__main__":
    main()
