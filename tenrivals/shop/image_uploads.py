"""Downscale + recompress raster uploads so storefront media is not multi‑MB over S3."""

from __future__ import annotations

import io
from pathlib import Path

from django.core.files.base import ContentFile
from PIL import Image, ImageOps

# Do not touch tiny assets (icons, accidental small files).
_MIN_BYTES_TO_PROCESS = 48_000
# Re-encode if still large after resize (heavy PNG screenshots).
_MAX_BYTES_KEEP_ORIGINAL = 380_000


def _rgba_to_rgb(im: Image.Image) -> Image.Image:
    if im.mode in ('RGBA', 'LA'):
        bg = Image.new('RGB', im.size, (255, 255, 255))
        mask = im.split()[-1] if im.mode == 'RGBA' else None
        bg.paste(im, mask=mask)
        return bg
    if im.mode != 'RGB':
        return im.convert('RGB')
    return im


def optimize_raster_upload(
    file_obj,
    *,
    max_width: int,
    max_height: int,
    jpeg_quality: int = 86,
) -> ContentFile | None:
    """
    Return a JPEG ContentFile scaled to fit inside max_width x max_height, or None to keep
    the original (SVG, GIF animation, unreadable file, already small).
    """
    name = getattr(file_obj, 'name', '') or ''
    lower = name.lower()
    if lower.endswith('.svg'):
        return None

    size = getattr(file_obj, 'size', None) or 0
    if size and size < _MIN_BYTES_TO_PROCESS:
        return None

    try:
        file_obj.seek(0)
        raw = file_obj.read()
    except Exception:
        return None

    if len(raw) < _MIN_BYTES_TO_PROCESS:
        return None

    try:
        im = Image.open(io.BytesIO(raw))
    except Exception:
        return None

    if getattr(im, 'is_animated', False):
        return None

    try:
        im = ImageOps.exif_transpose(im)
    except Exception:
        pass

    w, h = im.size
    if w < 1 or h < 1:
        return None

    scale = min(max_width / w, max_height / h, 1.0)
    if scale < 1.0:
        nw = max(1, int(w * scale))
        nh = max(1, int(h * scale))
        # Large downscales: LANCZOS is slow on big sources (e.g. multi‑MB PNG on small dynos).
        resample = Image.Resampling.BILINEAR if scale < 0.45 else Image.Resampling.LANCZOS
        im = im.resize((nw, nh), resample)
    elif len(raw) <= _MAX_BYTES_KEEP_ORIGINAL:
        return None

    im = _rgba_to_rgb(im)

    buf = io.BytesIO()
    im.save(
        buf,
        format='JPEG',
        quality=jpeg_quality,
        optimize=True,
        progressive=False,
    )
    out = buf.getvalue()
    if len(out) >= len(raw) * 0.98 and scale >= 1.0:
        return None

    stem = _safe_stem(name)
    return ContentFile(out, name=f'{stem}_web.jpg')


def _safe_stem(name: str) -> str:
    stem = Path(name).stem
    return (stem or 'image')[:100]


def assign_optimized_imagefield(field, *, max_width: int, max_height: int) -> None:
    """Replace ImageField file in-place before save (mutates `field`)."""
    if not field:
        return
    new_file = optimize_raster_upload(field, max_width=max_width, max_height=max_height)
    if new_file is None:
        return
    field.save(new_file.name, new_file, save=False)


def assign_optimized_filefield(field, *, max_width: int, max_height: int) -> None:
    """Same for FileField storing raster uploads."""
    assign_optimized_imagefield(field, max_width=max_width, max_height=max_height)


def imagefield_changed(prev, new_instance, field_name: str) -> bool:
    """True when a new file was uploaded (or first assignment). `prev` may be None on create."""
    new_val = getattr(new_instance, field_name, None)
    if not new_val:
        return False
    if prev is None:
        return True
    old_val = getattr(prev, field_name, None)
    return (old_val.name if old_val else '') != (new_val.name if new_val else '')
