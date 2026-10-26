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
import shutil
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
TEMPLATE_IDS = ["product-hero", "offer-badge", "ugc-caption", "range-lineup", "range-card", "brand-block", "packshot",
                "type-bold"]
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

WEAR = ("The first attached image is the client's real product. Create a new photorealistic advertising photograph of "
        "an original AI-generated model wearing exactly this product: keep its colour, cut, fabric, print and logo "
        "identical. Any other attached images are references for pose, framing, lighting and styling only: do not "
        "reproduce any person, face, text or logo from them. Keep the upper quarter calm and uncluttered for a headline. "
        "No added text, no watermark.")
PLACE = ("The first attached image is the client's real product. Create a photorealistic advertising photograph "
         "with exactly this product placed naturally in the scene described: keep its packaging shape, colours, label, "
         "printed text and logo identical (never redraw, translate or invent label text), light and shadows matching "
         "the scene, the product sharp and clearly visible, about a third of the frame, in the lower half. Keep the "
         "upper third calm and uncluttered for a headline. Any other attached images are references for mood and "
         "composition only: do not reproduce any person, text, logo or product from them. No added text, no watermark.")
REFS = (" The attached images are references for mood, lighting, composition and styling only: do not reproduce any "
        "person, text, logo or product from them.")


def background(prompt: str, out: Path, w: int, h: int, colors: list[str], seed: int = 1, product: bool = True,
               refs: list[str] = (), wear: Optional[str] = None, hold: Optional[str] = None) -> str:
    """Generate the scene; returns the model/source used. Never raises: last resort is a brand gradient.
    product=False (no photo to composite, e.g. a university or a service) asks for headline room instead.
    refs: reference photos for ChatGPT. wear: photo of a wearable product; ChatGPT then puts it on a new AI model
    (source "chatgpt-model": the product is already in the picture, so the template skips the product layer).
    hold: the real product photo; the model places it in the scene itself (natural light and shadows, unlike a
    pasted cut-out): source "chatgpt-product" / "gpt-image-product", and the template skips the product layer."""
    room = "empty space for a product" if product else "a calm, uncluttered area for a headline"
    full = f"{prompt.rstrip(' .')}. Photorealistic advertising photograph, {room}. {NEGATIVE}."
    if wear and _codex(f"{prompt.rstrip(' .')}. {WEAR}", out, w, h, [wear, *refs]):
        return "chatgpt-model"
    if hold and _codex(f"{prompt.rstrip(' .')}. {PLACE}", out, w, h, [hold, *refs]):
        return "chatgpt-product"
    if hold and _openai_edit(f"{prompt.rstrip(' .')}. {PLACE}", hold, out, w, h):
        return "gpt-image-product"
    if _codex(full + (REFS if refs else ""), out, w, h, list(refs)):
        return "chatgpt"
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


def _openai_edit(prompt: str, image: str, out: Path, w: int, h: int) -> bool:
    """OpenAI image edit (gpt-image-1) with the product photo as input; needs OPENAI_API_KEY. False when unavailable."""
    if not os.getenv("OPENAI_API_KEY"):
        return False
    try:
        from openai import OpenAI
        size = "1024x1536" if h > w * 1.15 else "1536x1024" if w > h * 1.15 else "1024x1024"
        with open(image, "rb") as f:
            r = OpenAI(timeout=300).images.edit(model=os.getenv("RUNE_IMAGE_EDIT_MODEL", "gpt-image-1"), image=[f],
                                               prompt=prompt, size=size)
        out.write_bytes(base64.b64decode(r.data[0].b64_json))
        return True
    except Exception:
        return False


def can_place_product() -> bool:
    """An image model that takes the product photo as input is available (and not switched off)."""
    return os.getenv("RUNE_PRODUCT_IN_SCENE", "1") == "1" and (codex_on() or bool(os.getenv("OPENAI_API_KEY")))


_POLLINATIONS = threading.Lock()
_CODEX = threading.Semaphore(4)  # ponytail: 4 parallel tested fine on ChatGPT Go (4 images ~2 min); lower if it rate-limits


CODEX_DOWN: dict = {}  # {"why": ..., "until": ...} once ChatGPT refuses (usage limit, signed out): stop asking


def codex_on() -> bool:
    return os.getenv("RUNE_IMAGE_CODEX") == "1" and bool(shutil.which("codex")) and not CODEX_DOWN


def _codex(prompt: str, out: Path, w: int, h: int, images: list[str] = ()) -> bool:
    """ChatGPT-plan image generation via the Codex CLI (`codex exec`, logged in with ChatGPT), ~80 s an image.
    Opt-in with RUNE_IMAGE_CODEX=1."""
    if not codex_on():
        return False
    shape = "landscape" if w > h else "portrait" if h > w else "square"
    ask = (f"Generate one {shape} image with your image generation tool: {prompt} "
           f"Save it as {out.name} in the current directory. Reply only with the saved path.")
    out.unlink(missing_ok=True)  # codex refuses to overwrite
    try:
        with _CODEX:
            # low effort: the agent only relays one prompt to the image tool (44 s vs ~80 s at the user's "high")
            proc = subprocess.run([shutil.which("codex"), "exec", "--skip-git-repo-check", "--ephemeral",
                                   "-c", "model_reasoning_effort=low", "-C", str(out.parent),
                                   "--sandbox", "workspace-write", ask, *(["-i", *images] if images else [])],
                                  input=b"", capture_output=True, timeout=600)
    except Exception:
        return False
    if out.exists() and out.stat().st_size > 0:
        return True
    said = (proc.stdout + proc.stderr).decode("utf-8", "replace")
    m = re.search(r"usage limit", said, re.I)
    until = re.search(r"try again at ([^\n]+?)\.?\s*$", said, re.I | re.M)
    if m or re.search(r"not logged in|log ?in required|unauthori[sz]ed", said, re.I):
        CODEX_DOWN.update({"why": "ChatGPT image limit reached" if m else "Codex isn't signed in to ChatGPT",
                           "until": until.group(1).strip() if until else ""})
    return False


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

NOT_A_PRODUCT = re.compile(r"(?<![a-z])(favicon|icon|logo|sprite|apple-touch|android-chrome|placeholder|avatar)s?(?![a-z])",
                           re.I)  # whole words: "silicone-case.jpg" is a product
MIN_PRODUCT_SIDE = 400


def product_photo_ok(src: str) -> bool:
    """Is this plausibly a real product photo? Sites hand out favicons, app icons and logos as share images
    (nike.com's og image is a 192px swoosh), and a logo composited as "the product" makes a product-less ad."""
    if not src or NOT_A_PRODUCT.search(re.split(r"[\\/]", src.split("?")[0])[-1]):  # the file name only
        return False
    try:
        from io import BytesIO
        from PIL import Image
        src = resolve(src)
        raw = httpx.get(src, timeout=30, follow_redirects=True).content if src.startswith("http") else Path(src).read_bytes()
        with Image.open(BytesIO(raw)) as im:
            return min(im.size) >= MIN_PRODUCT_SIDE
    except Exception:
        return False

def product_cutout(src: str, out: Path) -> Optional[str]:
    """Background-removed product PNG (rembg); the original photo if rembg isn't available. `out` is a cache:
    callers name it per source photo."""
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
        with Image.open(out) as cut:  # crop the transparent padding, or the product renders tiny
            box = cut.getchannel("A").getbbox()
            if box:
                cut.crop(box).save(out)
    except Exception:
        out.write_bytes(raw)
    return str(out)


# ---------- layer 3: text/brand via HTML template ----------

def layout(ratio: str, w: int, h: int) -> dict:
    """Pixel geometry per ratio. 9:16 keeps all text between 14% from the top and 35% from the bottom."""
    u = min(w, h) / 100
    if ratio == "9:16":
        return dict(U=u, PAD=6 * u, TOP=0.14 * h, CTA_TOP=0.65 * h - 16 * u, CAP=0.37 * h,
                    PROD_BOTTOM=0.04 * h, PROD_H=0.30 * h, PROD_CLASS="", BODY_CLASS="tall")
    if ratio == "1.91:1":
        return dict(U=u, PAD=5 * u, TOP=5 * u, CTA_TOP=h - 14 * u, CAP=16 * u,
                    PROD_BOTTOM=4 * u, PROD_H=0.78 * h, PROD_CLASS="right", BODY_CLASS="wide")
    return dict(U=u, PAD=6 * u, TOP=5 * u, CTA_TOP=h - 14 * u, CAP=18 * u,
                PROD_BOTTOM=4 * u, PROD_H=(0.46 if ratio == "4:5" else 0.5) * h, PROD_CLASS="right", BODY_CLASS="")


def _lum(hex_: str) -> float:
    r, g, b = (int(hex_.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4))
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def contrast(a: str, b: str) -> float:
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


_HUES = [(15, "red"), (40, "orange"), (65, "yellow"), (160, "green"), (195, "teal"), (255, "blue"),
         (285, "violet"), (330, "magenta"), (360, "red")]


def colour_name(hex_: str) -> str:
    """'#112337' -> 'navy blue': image models ignore hex codes but follow colour words."""
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", hex_ or ""):
        return hex_
    import colorsys
    h, l, s = colorsys.rgb_to_hls(*(int(hex_[i:i + 2], 16) / 255 for i in (1, 3, 5)))
    deg = h * 360
    if s < 0.12 or l < 0.06 or l > 0.96:
        return ("black" if l < 0.12 else "charcoal" if l < 0.3 else "grey" if l < 0.6 else
                "light grey" if l < 0.9 else "white")
    hue = next(n for top, n in _HUES if deg < top)
    if 15 <= deg < 50 and l < 0.35:
        return "brown"
    if 20 <= deg < 60 and l > 0.7 and s < 0.6:
        return "beige"
    if hue == "blue" and l < 0.3:
        return "navy blue"
    shade = "deep" if l < 0.25 else "dark" if l < 0.4 else "pale" if l > 0.8 else "light" if l > 0.65 else ""
    tone = "vivid" if not shade and s > 0.7 else "muted" if s < 0.35 else ""
    return " ".join(x for x in (shade or tone, hue) if x)


def brand_pair(colors: list[str]) -> tuple[str, str]:
    """(c1, c2): c2 is the CTA button fill, c1 its text. Guarantees readable contrast (WCAG >= 4.5)."""
    valid = [c for c in colors if re.fullmatch(r"#[0-9a-fA-F]{6}", c or "")]
    c1 = valid[0] if valid else "#1F2D4D"
    c2 = next((c for c in valid[1:] if contrast(c1, c) >= 4.5), None)
    return c1, c2 or ("#FFFFFF" if _lum(c1) < 0.4 else "#111318")


def offer_html(offer, currency_symbol: str = "", lang: str = "en") -> str:
    """The offer block: deal, was -> now price, coupon code, last day. '' without an offer (or once it's over)."""
    if not offer or (offer.ends and offer.ends < __import__("datetime").date.today()):
        return ""
    money = lambda v: f"{currency_symbol}{v:,.0f}"
    parts = [f'<span class="deal">{html.escape(offer.short())}</span>']
    if offer.was_price and offer.now_price and offer.now_price < offer.was_price:
        parts.append(f"<s>{money(offer.was_price)}</s><b>{money(offer.now_price)}</b>")
    from core.langs import words
    code_word, ends_word, _ = words(lang)
    if offer.code:
        parts.append(f'<span class="code">{html.escape(code_word)} {html.escape(offer.code)}</span>')
    if offer.ends:  # English: "Ends 9 Nov"; elsewhere a numeric date reads in every script
        day = f"{offer.ends.day} {offer.ends:%b}" if lang[:2] == "en" else f"{offer.ends.day}/{offer.ends.month}"
        parts.append(f'<span class="ends">{html.escape(ends_word)} {day}</span>')
    return "".join(parts)


def cta_label(cta: str, lang: str = "en") -> str:
    from core.langs import words
    return words(lang)[2].get(cta) or cta.replace("_", " ").capitalize()


def fill_template(template_id: str, ratio: str, w: int, h: int, *, bg: str, product: Optional[str], headline: str,
                  sub: str, cta: str, colors: list[str], font: str, logo: Optional[str], brand_name: str,
                  caption: str = "", show_cta: bool = True, products: list[str] = (), tags: list[str] = (),
                  offer: str = "", lang: str = "en", stars: str = "") -> str:
    """products: cut-outs for the range line-up (the middle one drawn tallest); tags: short benefit chips;
    offer: offer_html(); lang: script font + text direction; stars: a REAL rating line, never invented."""
    from core import langs
    script = langs.lang(lang)
    families = list(dict.fromkeys(f for f in (font or "Inter", script.font) if f))
    font_link = ('<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
                 + "&".join(f"family={quote(f)}:wght@400;500;700;800;900" for f in families) + '&display=block">')
    g = layout(ratio, w, h)
    c1, c2 = brand_pair(colors)
    logo_html = (f'<img class="logo" src="{Path(resolve(logo)).as_uri() if not logo.startswith("http") else logo}">'
                 if logo else f'<div class="brand">{html.escape(brand_name)}</div>')
    vals = {**{k: (f"{v:.0f}" if isinstance(v, float) else v) for k, v in g.items()},
            "W": w, "H": h, "C1": c1, "C2": c2, "FONT": html.escape(font or "Inter"),
            "INK": "#FFFFFF" if _lum(c1) < 0.45 else "#111318",  # text on a brand-colour panel
            "BASE_CSS": (TEMPLATES / "_base.css").read_text(encoding="utf-8"),
            "BG": Path(bg).as_uri(), "PRODUCT": Path(product).as_uri() if product else "",
            "PROD_CLASS": g["PROD_CLASS"] + ("" if product else " hide"),
            "LOGO": logo_html, "HEADLINE": html.escape(headline), "SUB": html.escape(sub),
            "BADGE_CLASS": "" if sub else "hide", "CTA": html.escape(cta_label(cta, lang)),
            "CTA_CLASS": "" if show_cta else "hide",
            "CAPTION": f"<span>{html.escape(caption)}</span>" if caption else "",
            "PRODUCTS": "".join(f'<img class="{"mid" if i == len(products) // 2 else ""}" src="{Path(p).as_uri()}">'
                                for i, p in enumerate(products)),
            "TAGS": "".join(f"<span>{html.escape(t)}</span>" for t in tags),
            "FONT_LINK": font_link, "FONT2": html.escape(script.font), "DIR": "rtl" if script.rtl else "ltr",
            "OFFER": offer, "STARS": html.escape(stars),
            "RANGE_CLASS": "many" if len(products) > 3 else ""}
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
            try:
                page.evaluate("document.fonts.ready.then(() => true)")
            except Exception:
                pass
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


def voiceover(text: str, out: Path, voice: str = "en-IN-NeerjaNeural") -> Optional[float]:
    """Edge-TTS (free, no key) in `voice` (core.langs.voice). Returns the clip length, or None when TTS is
    unavailable."""
    try:
        import edge_tts
        save = lambda: asyncio.run(edge_tts.Communicate(text, voice).save(str(out)))
        with ThreadPoolExecutor(1) as ex:  # own thread: Playwright's sync API keeps an event loop on this one
            ex.submit(save).result(timeout=60)
        return media_duration(out) or None
    except Exception:
        return None


def captions(text: str, n_words: int = 6, lang: str = "en") -> list[str]:
    from core.langs import NO_SPACES
    if lang in NO_SPACES or (" " not in text.strip() and len(text) > 20):  # Japanese, Chinese, Thai: by characters
        return [text[i:i + 14] for i in range(0, len(text), 14)] or [text]
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
