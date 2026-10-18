"""M6 generation helpers: background image, product cutout, HTML->PNG render, voiceover, template video.

Rule: the diffusion model only ever draws the background scene. The real product photo is cut out and
composited, and every word, logo and CTA comes from an HTML template rendered by Playwright.
"""
from __future__ import annotations

import asyncio
import base64
import html
import os
import re
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from string import Template
from typing import Optional
from urllib.parse import quote

import httpx

from core import llm

BACKEND = Path(__file__).resolve().parent.parent
TEMPLATES = BACKEND / "templates"
MEDIA = BACKEND / "media"
TEMPLATE_IDS = ["product-hero", "offer-badge", "ugc-caption"]
NEGATIVE = "no text, no letters, no words, no logos, no labels, no watermark, no products, no packaging"


def rel(p: Path) -> str:
    """Paths stored on creatives are relative to backend/ (like the fixtures: media/<client>/...)."""
    return p.resolve().relative_to(BACKEND).as_posix()


def resolve(path_or_url: str) -> str:
    if path_or_url.startswith(("http://", "https://")):
        return path_or_url
    p = Path(path_or_url)
    return str(p if p.is_absolute() else BACKEND / p)


# ---------- layer 1: background scene ----------

def background(prompt: str, out: Path, w: int, h: int, colors: list[str], seed: int = 1) -> str:
    """Generate the scene; returns the model/source used. Never raises: last resort is a brand gradient."""
    full = f"{prompt}. Photorealistic advertising photograph, empty space for a product. {NEGATIVE}."
    try:
        r = llm.client().images.generate(model=llm.model_for("image"), prompt=full, size="1024x1024", n=1)
        d = r.data[0]
        data = base64.b64decode(d.b64_json) if getattr(d, "b64_json", None) else httpx.get(d.url, timeout=60).content
        out.write_bytes(data)
        return llm.model_for("image")
    except Exception:
        pass
    k = min(1.0, 1280 / max(w, h))  # Pollinations rejects large sizes; the template's cover-fit scales it up
    url = (f"https://image.pollinations.ai/prompt/{quote(full)}?width={int(w * k)}&height={int(h * 1.06 * k)}&nologo=true"
           f"&seed={seed}&negative_prompt={quote(NEGATIVE)}")
    headers = {"Authorization": f"Bearer {os.getenv('POLLINATIONS_TOKEN')}"} if os.getenv("POLLINATIONS_TOKEN") else {}
    for attempt in range(3):
        try:
            with _POLLINATIONS:  # anonymous tier: 1 image / 45 s per IP, so one request at a time
                r = httpx.get(url, timeout=120, follow_redirects=True, headers=headers)
                if r.status_code in (402, 429):
                    m = re.search(r"[Rr]etry after (\d+)", r.text)
                    time.sleep(min(60, int(m.group(1)) + 1 if m else 46))
                    continue
            r.raise_for_status()
            if r.headers.get("content-type", "").startswith("image/"):
                out.write_bytes(r.content)
                _crop_bottom(out, 0.06)  # anonymous images carry a watermark in the bottom corner
                return "pollinations"
        except Exception:
            time.sleep(3 * (attempt + 1))
    gradient(out, w, h, colors)
    return "gradient"


_POLLINATIONS = threading.Lock()


def _crop_bottom(path: Path, frac: float) -> None:
    from PIL import Image
    with Image.open(path) as im:
        im = im.convert("RGB").crop((0, 0, im.width, int(im.height * (1 - frac))))
    im.save(path, format="PNG")


def gradient(out: Path, w: int, h: int, colors: list[str]) -> None:
    from PIL import Image
    c = [tuple(int(x.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)) for x in (colors or ["#1F2D4D", "#E8D9C4"])[:2]]
    a, b = c[0], c[-1]
    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(h):
        t = y / max(1, h - 1)
        row = tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))
        for x in range(w):
            px[x, y] = row
    img.save(out)


# ---------- layer 2: real product photo ----------

def product_cutout(src: str, out: Path) -> Optional[str]:
    """Background-removed product PNG (rembg); the original photo if rembg isn't available."""
    if out.exists():
        return str(out)
    src = resolve(src)
    raw = httpx.get(src, timeout=60, follow_redirects=True).content if src.startswith("http") else Path(src).read_bytes()
    from io import BytesIO
    from PIL import Image
    with Image.open(BytesIO(raw)) as im:
        if min(im.size) < 64:
            raise ValueError(f"product photo is only {im.size[0]}x{im.size[1]}px")
    try:
        from rembg import remove
        out.write_bytes(remove(raw))
    except Exception:
        out.write_bytes(raw)
    return str(out)


# ---------- layer 3: text/brand via HTML template ----------

def layout(ratio: str, w: int, h: int) -> dict:
    """Pixel geometry per ratio. 9:16 keeps all text between 14% from the top and 35% from the bottom."""
    u = min(w, h) / 100
    if ratio == "9:16":
        return dict(U=u, PAD=6 * u, TOP=0.14 * h, CTA_TOP=0.65 * h - 12 * u, CAP=0.37 * h,
                    PROD_BOTTOM=0.04 * h, PROD_H=0.30 * h, PROD_CLASS="", BODY_CLASS="tall")
    if ratio == "1.91:1":
        return dict(U=u, PAD=5 * u, TOP=5 * u, CTA_TOP=h - 14 * u, CAP=16 * u,
                    PROD_BOTTOM=4 * u, PROD_H=0.78 * h, PROD_CLASS="right", BODY_CLASS="wide")
    return dict(U=u, PAD=6 * u, TOP=5 * u, CTA_TOP=h - 14 * u, CAP=18 * u,
                PROD_BOTTOM=4 * u, PROD_H=(0.46 if ratio == "4:5" else 0.5) * h, PROD_CLASS="right", BODY_CLASS="")


def cta_label(cta: str) -> str:
    return cta.replace("_", " ").capitalize()


def fill_template(template_id: str, ratio: str, w: int, h: int, *, bg: str, product: Optional[str], headline: str,
                  sub: str, cta: str, colors: list[str], font: str, logo: Optional[str], brand_name: str,
                  caption: str = "", show_cta: bool = True) -> str:
    g = layout(ratio, w, h)
    c1, c2 = (colors + ["#1F2D4D", "#E8D9C4"])[:2] if colors else ("#1F2D4D", "#E8D9C4")
    logo_html = (f'<img class="logo" src="{Path(resolve(logo)).as_uri() if not logo.startswith("http") else logo}">'
                 if logo else f'<div class="brand">{html.escape(brand_name)}</div>')
    vals = {**{k: (f"{v:.0f}" if isinstance(v, float) else v) for k, v in g.items()},
            "W": w, "H": h, "C1": c1, "C2": c2, "FONT": html.escape(font or "Inter"),
            "BASE_CSS": (TEMPLATES / "_base.css").read_text(encoding="utf-8"),
            "BG": Path(bg).as_uri(), "PRODUCT": Path(product).as_uri() if product else "",
            "PROD_CLASS": g["PROD_CLASS"] + ("" if product else " hide"),
            "LOGO": logo_html, "HEADLINE": html.escape(headline), "SUB": html.escape(sub),
            "BADGE_CLASS": "" if sub else "hide", "CTA": html.escape(cta_label(cta)),
            "CTA_CLASS": "" if show_cta else "hide",
            "CAPTION": f"<span>{html.escape(caption)}</span>" if caption else ""}
    return Template((TEMPLATES / f"{template_id}.html").read_text(encoding="utf-8")).safe_substitute(vals)


class Renderer:
    """One headless Chromium for the whole step; screenshots HTML at the exact pixel size."""

    def __enter__(self):
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch()
        return self

    def render(self, page_html: str, out: Path, w: int, h: int) -> None:
        src = out.with_suffix(".html")
        src.write_text(page_html, encoding="utf-8")
        page = self._browser.new_page(viewport={"width": w, "height": h})
        try:
            page.goto(src.as_uri(), wait_until="load")
            page.screenshot(path=str(out), clip={"x": 0, "y": 0, "width": w, "height": h})
        finally:
            page.close()
            src.unlink(missing_ok=True)

    def __exit__(self, *exc):
        self._browser.close()
        self._pw.stop()


def composite_only(bg: str, product: Optional[str], out: Path, w: int, h: int) -> None:
    """Text-free fallback when Playwright can't run: background cover-cropped + product. QA will flag it."""
    from PIL import Image, ImageOps
    img = ImageOps.fit(Image.open(bg).convert("RGB"), (w, h))
    if product:
        p = Image.open(product).convert("RGBA")
        p.thumbnail((int(w * 0.6), int(h * 0.5)))
        img.paste(p, ((w - p.width) // 2, h - p.height - h // 20), p)
    img.save(out)


# ---------- video: template motion ----------

def ffmpeg() -> str:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"


def media_duration(path: Path) -> float:
    err = subprocess.run([ffmpeg(), "-i", str(path)], capture_output=True, text=True).stderr
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", err)
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else 0.0


def voiceover(text: str, out: Path, lang_geo: str = "en-IN") -> Optional[float]:
    """Edge-TTS (free, no key). Returns the clip length, or None when TTS is unavailable."""
    voices = {"en-IN": "en-IN-NeerjaNeural", "en-US": "en-US-AriaNeural", "en-GB": "en-GB-SoniaNeural"}
    try:
        import edge_tts
        save = lambda: asyncio.run(edge_tts.Communicate(text, voices.get(lang_geo, "en-US-AriaNeural")).save(str(out)))
        with ThreadPoolExecutor(1) as ex:  # own thread: Playwright's sync API keeps an event loop on this one
            ex.submit(save).result(timeout=60)
        return media_duration(out) or None
    except Exception:
        return None


def captions(text: str, n_words: int = 6) -> list[str]:
    words = text.split()
    return [" ".join(words[i:i + n_words]) for i in range(0, len(words), n_words)] or [text]


def motion_video(frames: list[tuple[Path, float]], audio: Optional[Path], out: Path, w: int, h: int, fps: int = 30) -> None:
    """Slow continuous push-in across caption frames, concatenated, with voiceover (or silence)."""
    cmd = [ffmpeg(), "-y", "-loglevel", "error"]
    for f, d in frames:
        cmd += ["-loop", "1", "-framerate", str(fps), "-t", f"{d:.2f}", "-i", str(f)]
    total = sum(d for _, d in frames)
    if audio:
        cmd += ["-i", str(audio)]
    else:
        cmd += ["-f", "lavfi", "-t", f"{total:.2f}", "-i", "anullsrc=r=44100:cl=stereo"]
    parts, start = [], 0
    for i, (_, d) in enumerate(frames):
        z = f"min(1+0.0004*(on+{start}),1.06)"
        parts.append(f"[{i}:v]scale={w}:{h},zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={w}x{h}:fps={fps},"
                     f"setsar=1[v{i}]")
        start += int(d * fps)
    graph = ";".join(parts) + ";" + "".join(f"[v{i}]" for i in range(len(frames))) + f"concat=n={len(frames)}:v=1:a=0[v]"
    a = len(frames)
    cmd += ["-filter_complex", graph + f";[{a}:a]apad[a]", "-map", "[v]", "-map", "[a]", "-t", f"{total:.2f}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(fps), "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart", str(out)]
    subprocess.run(cmd, check=True, capture_output=True)
