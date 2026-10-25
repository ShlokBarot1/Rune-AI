"""Client-supplied creatives: the client uploads finished images/videos and the agents run ads on them.

No generative media anywhere on this path: no image model, no product cutout, no template, no voiceover.
The files are only checked (size, aspect, duration) and, when their aspect is not one Meta places well,
letterboxed onto the nearest supported ratio (never cropped). Copy comes from the client when they gave it,
otherwise from the hooks agent; both are editable at the hooks and creatives gates.

Turned on by intake["creatives"] = [{path, primary_text?, headline?, description?, cta?}, ...], the paths being
files uploaded through POST /malav/runs/{cid}/upload (anything outside that run's uploads folder is ignored).
"""
from __future__ import annotations

import math
import re
import subprocess
from pathlib import Path
from typing import Optional

from pydantic import BaseModel

from core import bus
from core.schemas import RATIO_SIZES, AdCopy, CampaignBlueprint, Creative, QAResult, Ratio
from malav import media

AGENT = "malav.packager"
SOURCE = "client-upload"
VIDEO_EXT = (".mp4", ".mov", ".m4v", ".webm")
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp")
ASPECT_TOLERANCE = 0.03     # within 3% of a supported ratio: used as is
MIN_SIDE = 500              # Meta rejects or blurs smaller assets
VIDEO_MAX_S = 240.0


class Asset(BaseModel):
    path: str                       # relative to backend/ (media/<cid>/uploads/...)
    kind: str                       # image | video
    ratio: Ratio
    width: int
    height: int
    duration_s: Optional[float] = None
    thumbnail_path: Optional[str] = None
    client_copy: dict = {}                # whatever copy the client typed for this file
    issues: list[str] = []          # deterministic QA failures


def uploads(state: dict) -> list[dict]:
    """The client's creatives for this run, keeping only files inside its own uploads folder."""
    cid = re.sub(r"[^A-Za-z0-9_-]", "", state.get("client_id", ""))
    root = (media.MEDIA / cid / "uploads").resolve()
    out = []
    for item in (state.get("intake") or {}).get("creatives") or []:
        item = item if isinstance(item, dict) else {"path": str(item)}
        p = Path(media.resolve(str(item.get("path", "")))).resolve()
        if p.is_relative_to(root) and p.is_file() and p.suffix.lower() in IMAGE_EXT + VIDEO_EXT:
            out.append({**item, "path": media.rel(p)})
    return out


def nearest_ratio(w: int, h: int) -> tuple[Ratio, float]:
    """(ratio, relative aspect error)."""
    best = min(RATIO_SIZES, key=lambda r: abs(math.log((w / h) / (RATIO_SIZES[r][0] / RATIO_SIZES[r][1]))))
    rw, rh = RATIO_SIZES[best]
    return best, abs((w / h) / (rw / rh) - 1)


def _video_info(path: Path) -> tuple[int, int, float]:
    err = subprocess.run([media.ffmpeg(), "-i", str(path)], capture_output=True, text=True, errors="replace").stderr
    m = re.search(r"Video:.*?\b(\d{2,5})x(\d{2,5})\b", err)
    if not m:
        raise ValueError("no video stream")
    w, h = int(m.group(1)), int(m.group(2))
    if re.search(r"rotat(e\s*:\s*-?|ion of -?)(90|270)", err):  # phone videos store portrait as rotated landscape
        w, h = h, w
    return w, h, media.media_duration(path)


def _letterbox_image(src: Path, out: Path, ratio: str) -> tuple[int, int]:
    """Fit the whole image onto the ratio's canvas over a blurred, dimmed copy of itself (nothing cropped)."""
    from PIL import Image, ImageEnhance, ImageFilter, ImageOps
    W, H = RATIO_SIZES[ratio]
    with Image.open(src) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        bg = ImageEnhance.Brightness(ImageOps.fit(im, (W, H)).filter(ImageFilter.GaussianBlur(40))).enhance(0.6)
        fg = im.copy()
        k = min(W / fg.width, H / fg.height)
        fg = fg.resize((max(1, round(fg.width * k)), max(1, round(fg.height * k))), Image.LANCZOS)
        bg.paste(fg, ((W - fg.width) // 2, (H - fg.height) // 2))
    bg.save(out, "PNG")
    return W, H


def _letterbox_video(src: Path, out: Path, ratio: str) -> None:
    W, H = RATIO_SIZES[ratio]
    subprocess.run([media.ffmpeg(), "-y", "-loglevel", "error", "-i", str(src), "-vf",
                    f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:black,setsar=1",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart", str(out)],
                   check=True, capture_output=True)


def probe(cid: str, item: dict) -> Asset:
    """Inspect one upload; letterbox it when its aspect is off. Never raises: problems land in Asset.issues."""
    src = Path(media.resolve(item["path"]))
    kind = "video" if src.suffix.lower() in VIDEO_EXT else "image"
    client_copy = {k: str(item[k]).strip() for k in ("primary_text", "headline", "description", "cta") if item.get(k)}
    try:
        if kind == "image":
            from PIL import Image, ImageOps
            with Image.open(src) as im:
                w, h = ImageOps.exif_transpose(im).size
            duration = None
        else:
            w, h, duration = _video_info(src)
    except Exception as e:
        return Asset(path=item["path"], kind=kind, ratio="1:1", width=0, height=0, client_copy=client_copy,
                     issues=[f"unreadable file ({e.__class__.__name__})"])
    ratio, err = nearest_ratio(w, h)
    path = src
    if err > ASPECT_TOLERANCE:
        out = src.with_name(f"{src.stem}_{ratio.replace(':', 'x').replace('.', '_')}{'.png' if kind == 'image' else '.mp4'}")
        try:
            if not out.exists():
                (_letterbox_image if kind == "image" else _letterbox_video)(src, out, ratio)
            path, (w, h) = out, RATIO_SIZES[ratio]
            bus.log(cid, AGENT, f"{src.name} is {w}x{h}-ish, not a Meta ratio: placed it on a {ratio} canvas (nothing cropped)")
        except Exception as e:
            bus.log(cid, AGENT, f"{src.name}: couldn't fit it to {ratio} ({e.__class__.__name__}); using it as is")
    issues = []
    if min(w, h) < MIN_SIDE:
        issues.append(f"too small ({w}x{h}px, needs {MIN_SIDE}px on the short side)")
    size = path.stat().st_size
    if kind == "image" and size > 30_000_000:
        issues.append("image over 30 MB")
    thumb = None
    if kind == "video":
        if not duration or not 1 <= duration <= VIDEO_MAX_S:
            issues.append(f"video length {duration or 0:.0f}s (Meta feed ads need 1-{VIDEO_MAX_S:.0f}s)")
        if size > 4_000_000_000:
            issues.append("video over 4 GB")
        thumb = path.with_name(f"{path.stem}_thumb.png")
        if not thumb.exists():
            subprocess.run([media.ffmpeg(), "-y", "-loglevel", "error", "-ss", f"{min(0.5, (duration or 1) / 2):.2f}",
                            "-i", str(path), "-frames:v", "1", str(thumb)], capture_output=True)
        if not thumb.exists():
            issues.append("couldn't extract a cover frame")
            thumb = None
    return Asset(path=media.rel(path), kind=kind, ratio=ratio, width=w, height=h, duration_s=duration,
                 thumbnail_path=media.rel(thumb) if thumb else None, client_copy=client_copy, issues=issues)


def probe_all(cid: str, items: list[dict]) -> list[Asset]:
    return [probe(cid, i) for i in items]


def deliverables(assets: list[Asset]) -> dict:
    """Intake slots the uploads answer: what the client gave is what runs."""
    return {"n_images": sum(a.kind == "image" for a in assets), "n_videos": sum(a.kind == "video" for a in assets),
            "ratios": sorted({a.ratio for a in assets}, key=list(RATIO_SIZES).index)}


def to_creative(a: Asset, *, creative_id: str, campaign_ref: str, adset_ref: str, ad_ref: str, hook_id: str,
                copy: AdCopy, landing_url: str, version: int = 1, parent_id: Optional[str] = None) -> Creative:
    c = {k: v for k, v in a.client_copy.items() if k != "cta"}
    cta = a.client_copy.get("cta", "").upper().replace(" ", "_")
    copy = copy.model_copy(update={**c, **({"cta": cta} if cta in AdCopy.model_fields["cta"].annotation.__args__ else {})})
    checks = {"file_exists": Path(media.resolve(a.path)).exists(), "readable": a.width > 0}
    checks |= {"min_size": not any("too small" in i for i in a.issues),
               "size_ok": not any(" over " in i for i in a.issues)}
    if a.kind == "video":
        checks |= {"duration_ok": not any("length" in i for i in a.issues), "thumbnail": bool(a.thumbnail_path)}
    ok = all(checks.values())
    qa = QAResult(score=90 if ok else 20, passed=ok, checks=checks,
                  notes="Your upload, used as is." if ok else "Replace this file: " + "; ".join(a.issues))
    return Creative(creative_id=creative_id, campaign_ref=campaign_ref, adset_ref=adset_ref, ad_ref=ad_ref,
                    hook_id=hook_id, kind=a.kind, ratio=a.ratio, width=a.width, height=a.height,
                    duration_s=round(a.duration_s, 2) if a.duration_s else None, path=a.path,
                    thumbnail_path=a.thumbnail_path, ad_copy=copy, landing_url=landing_url, model_used=SOURCE,
                    template_id=None, prompt="", qa=qa, version=version, parent_id=parent_id)


def match(bp: CampaignBlueprint, assets: list[Asset]) -> list[tuple[str, str, object, Asset]]:
    """(campaign_ref, adset_ref, AdPlan, asset) per planned ad: first unused upload of the same format and ratio,
    else the same format. Strategy plans exactly one ad per upload, so normally every upload lands once."""
    pool, out = list(assets), []
    for camp in bp.campaigns:
        for s in camp.adsets:
            for ad in s.ads:
                kind = "video" if ad.format == "video" else "image"
                a = (next((x for x in pool if x.kind == kind and x.ratio in ad.ratios), None)
                     or next((x for x in pool if x.kind == kind), None))
                if a:
                    pool.remove(a)
                    out.append((camp.ref, s.ref, ad, a))
    return out


def is_own(c: Creative) -> bool:
    return c.model_used == SOURCE
