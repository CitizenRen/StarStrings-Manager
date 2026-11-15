"""Generate the icon/image files the app and exe use, from the source art in assets/.

  assets/app-icon.png      -> generated/app-icon.ico       (the .exe file icon)
  assets/window-icon.png   -> generated/window-icon.ico    (window title bar / taskbar icon)
                           -> generated/titlebar-logo.png  (logo in the app's title bar)
  assets/github-mark.svg   -> generated/github-mark.png    (footer GitHub button)
  assets/ui-icons/*.svg    -> generated/ui-icons/*.png     (button icons, GitHub Octicons, MIT)

assets/window-icon.svg is the vector original of window-icon.png and isn't used by the build.
"""
from pathlib import Path

import pymupdf
from PIL import Image

ASSETS = Path(__file__).resolve().parent.parent / "assets"
OUT = ASSETS / "generated"
SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def square(img: Image.Image, size: int) -> Image.Image:
    """Fit an image into a transparent size x size square, centred, high-quality resample."""
    img = img.convert("RGBA")
    img.thumbnail((size, size), Image.LANCZOS)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(img, ((size - img.width) // 2, (size - img.height) // 2), img)
    return out


def render_svg(path: Path, size: int) -> Image.Image:
    page = pymupdf.open(path)[0]
    scale = size * 2 / max(page.rect.width, page.rect.height)  # supersample, then shrink
    pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=True)
    return square(Image.frombytes("RGBA", (pix.width, pix.height), pix.samples), size)


def main():
    (OUT / "ui-icons").mkdir(parents=True, exist_ok=True)

    Image.open(ASSETS / "app-icon.png").convert("RGBA").save(OUT / "app-icon.ico", sizes=SIZES)

    win = Image.open(ASSETS / "window-icon.png")
    # Each ico frame is resampled separately from the full-size source for crisp small sizes.
    frames = [square(win, s[0]) for s in SIZES]
    frames[-1].save(OUT / "window-icon.ico", sizes=SIZES, append_images=frames[:-1])
    square(win, 128).save(OUT / "titlebar-logo.png")

    render_svg(ASSETS / "github-mark.svg", 64).save(OUT / "github-mark.png")
    for svg in (ASSETS / "ui-icons").glob("*.svg"):
        render_svg(svg, 64).save(OUT / "ui-icons" / f"{svg.stem}.png")


if __name__ == "__main__":
    main()
    print("Generated files written to", OUT)
