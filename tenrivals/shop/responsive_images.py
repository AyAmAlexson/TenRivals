"""Width-limited WebP copies for storefront images.

HTML render must not open the original file. Variants are built on the Celery
worker (or by ``build_image_renditions``) and recorded in StoredImageRendition.
Until that row exists, the template keeps the original URL.
"""

from __future__ import annotations

import hashlib
import io
import logging
from dataclasses import dataclass

from django.conf import settings
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from PIL import Image, ImageOps

logger = logging.getLogger(__name__)

RENDITION_VERSION = 1
WIDTHS = (160, 320, 480, 640, 960, 1280, 1600, 2000)
_LONGEST_SIDE = 2000
_WEBP_QUALITY = 76
_CACHE_TTL = 60 * 60 * 24
_MISS_TTL = 90
_LOCK_TTL = 60 * 15
_SKIP_EXTENSIONS = ('.svg', '.gif')
_RASTER_EXTENSIONS = ('.jpg', '.jpeg', '.png', '.webp', '.bmp', '.tif', '.tiff')

PRODUCT_IMAGE_FIELDS = ('image_1', 'image_2', 'image_3', 'image_4', 'image_5')

_POS = 'img-rendition:v1:'
_NEG = 'img-rendition-miss:v1:'
_LOCK = 'img-rendition-lock:v1:'
_broker_failed = False


@dataclass(frozen=True)
class ImageRendition:
    src: str
    srcset: str
    full: str
    width: int
    height: int

    def __bool__(self) -> bool:
        return bool(self.src)


def image_rendition(image, default_width: int = 960) -> ImageRendition | None:
    """Public URL set for a FieldFile. Never opens the file."""
    name = _source_name(image)
    if not name:
        return None
    original = _public_url(name)
    if not original:
        return None
    if _extension(name) in _SKIP_EXTENSIONS:
        return ImageRendition(src=original, srcset='', full=original, width=0, height=0)

    meta = _lookup(name)
    variants = (meta or {}).get('variants') or {}
    if not variants:
        return ImageRendition(src=original, srcset='', full=original, width=0, height=0)

    pairs: list[tuple[int, str]] = []
    for key, stored in variants.items():
        try:
            width = int(key)
        except (TypeError, ValueError):
            continue
        url = _public_url(stored)
        if url:
            pairs.append((width, url))
    pairs.sort()
    if not pairs:
        return ImageRendition(src=original, srcset='', full=original, width=0, height=0)

    chosen = pairs[-1][1]
    for width, url in pairs:
        if width >= int(default_width):
            chosen = url
            break
    srcset = ', '.join(f'{url} {width}w' for width, url in pairs)
    return ImageRendition(
        src=chosen,
        srcset=srcset,
        full=pairs[-1][1],
        width=int(meta.get('width') or 0),
        height=int(meta.get('height') or 0),
    )


def schedule_build(source_name: str) -> None:
    """Enqueue one build. No-op when renditions are disabled, already stored, or already queued."""
    global _broker_failed
    name = (source_name or '').strip()
    if (
        not name
        or _broker_failed
        or not getattr(settings, 'SHOP_BUILD_IMAGE_RENDITIONS', False)
    ):
        return
    if _extension(name) in _SKIP_EXTENSIONS or _extension(name) not in _RASTER_EXTENSIONS:
        return
    if _cached_meta(name) is not None:
        return
    if not cache.add(_LOCK + name, 1, _LOCK_TTL):
        return
    try:
        from shop.tasks import build_image_rendition

        build_image_rendition.delay(name)
    except Exception:
        _broker_failed = True
        cache.delete(_LOCK + name)
        logger.exception('Could not enqueue image rendition for %s', name)


def schedule_saved_images(instance, field_names) -> None:
    for fname in field_names:
        field = getattr(instance, fname, None)
        name = _source_name(field)
        if name:
            schedule_build(name)


def prepare_product_image_uploads(instance) -> None:
    """Downscale a newly uploaded product photo before it is stored. Safe on the staff request."""
    from shop.image_uploads import assign_optimized_imagefield, imagefield_changed
    from shop.models import Product

    prev = None
    if getattr(instance, 'pk', None):
        prev = Product.objects.filter(pk=instance.pk).only(*PRODUCT_IMAGE_FIELDS).first()
    for fname in PRODUCT_IMAGE_FIELDS:
        field = getattr(instance, fname, None)
        if field and imagefield_changed(prev, instance, fname):
            assign_optimized_imagefield(field, max_width=1600, max_height=2000)


def build_rendition(source_name: str, *, force: bool = False) -> str:
    """
    Write WebP variants for ``source_name`` and store the row.

    Returns ``built``, ``skipped``, ``unsupported``, or ``missing``.
    """
    name = (source_name or '').strip()
    if not name:
        return 'missing'
    ext = _extension(name)
    if ext in _SKIP_EXTENSIONS or ext not in _RASTER_EXTENSIONS:
        _store(name, 0, 0, {})
        return 'unsupported'

    if not force:
        current = _row_meta(name)
        if current and int(current.get('version') or 0) == RENDITION_VERSION:
            cache.delete(_LOCK + name)
            return 'skipped'

    try:
        with default_storage.open(name, 'rb') as handle:
            raw = handle.read()
    except Exception:
        logger.warning('Image rendition source missing: %s', name, exc_info=True)
        return 'missing'

    try:
        im = Image.open(io.BytesIO(raw))
        if getattr(im, 'is_animated', False):
            _store(name, 0, 0, {})
            return 'unsupported'
        im = ImageOps.exif_transpose(im)
    except Exception:
        logger.exception('Could not read image %s', name)
        _store(name, 0, 0, {})
        return 'unsupported'

    im = _cap_longest_side(im)
    width, height = im.size
    if width < 1 or height < 1:
        _store(name, 0, 0, {})
        return 'unsupported'

    targets = [w for w in WIDTHS if w <= width]
    if not targets:
        targets = [width]
    variants: dict[str, str] = {}
    try:
        for target in targets:
            encoded = _encode_width(im, target)
            if encoded is None:
                continue
            path = _variant_path(name, target)
            _save_bytes(path, encoded)
            variants[str(target)] = path
    except Exception:
        logger.exception('Could not encode variants for %s', name)
        return 'missing'
    finally:
        im.close()

    if not variants:
        _store(name, width, height, {})
        return 'unsupported'
    _store(name, width, height, variants)
    return 'built'


def _lookup(name: str) -> dict | None:
    meta = _cached_meta(name)
    if meta is not None:
        return meta
    if cache.get(_NEG + name):
        return None
    meta = _row_meta(name)
    if meta is None:
        cache.set(_NEG + name, 1, _MISS_TTL)
        schedule_build(name)
        return None
    if int(meta.get('version') or 0) != RENDITION_VERSION:
        schedule_build(name)
        return None
    cache.set(_POS + name, meta, _CACHE_TTL)
    return meta


def _cached_meta(name: str) -> dict | None:
    hit = cache.get(_POS + name)
    if isinstance(hit, dict) and int(hit.get('version') or 0) == RENDITION_VERSION:
        return hit
    return None


def _row_meta(name: str) -> dict | None:
    from shop.models import StoredImageRendition

    row = (
        StoredImageRendition.objects.filter(source_name=name)
        .values('width', 'height', 'variants', 'version')
        .first()
    )
    if not row:
        return None
    return {
        'width': row['width'],
        'height': row['height'],
        'variants': row['variants'] or {},
        'version': row['version'],
    }


def _store(name: str, width: int, height: int, variants: dict) -> None:
    from shop.models import StoredImageRendition

    StoredImageRendition.objects.update_or_create(
        source_name=name,
        defaults={
            'width': width,
            'height': height,
            'variants': variants,
            'version': RENDITION_VERSION,
        },
    )
    cache.delete(_NEG + name)
    cache.delete(_LOCK + name)
    cache.set(
        _POS + name,
        {
            'width': width,
            'height': height,
            'variants': variants,
            'version': RENDITION_VERSION,
        },
        _CACHE_TTL,
    )


def _encode_width(im: Image.Image, target: int) -> bytes | None:
    if im.width == target:
        frame = im
    else:
        height = max(1, round(im.height * (target / im.width)))
        frame = im.resize((target, height), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    save_kwargs = {'format': 'WEBP', 'quality': _WEBP_QUALITY, 'method': 4}
    if frame.mode in ('RGBA', 'LA'):
        frame.save(buf, **save_kwargs)
    else:
        if frame.mode != 'RGB':
            frame = frame.convert('RGB')
        frame.save(buf, **save_kwargs)
    return buf.getvalue()


def _cap_longest_side(im: Image.Image) -> Image.Image:
    longest = max(im.size)
    if longest <= _LONGEST_SIDE:
        return im
    scale = _LONGEST_SIDE / longest
    size = (max(1, int(im.width * scale)), max(1, int(im.height * scale)))
    return im.resize(size, Image.Resampling.LANCZOS)


def _save_bytes(path: str, payload: bytes) -> None:
    if default_storage.exists(path):
        default_storage.delete(path)
    default_storage.save(path, ContentFile(payload))


def _variant_path(source_name: str, width: int) -> str:
    digest = hashlib.sha1(source_name.encode()).hexdigest()[:20]
    return f'shop/variants/{digest}/{width}.webp'


def _public_url(stored_name: str) -> str:
    try:
        return default_storage.url(stored_name)
    except Exception:
        logger.exception('Could not build URL for %s', stored_name)
        return ''


def _source_name(image) -> str:
    if not image:
        return ''
    return (getattr(image, 'name', None) or '').strip()


def _extension(name: str) -> str:
    dot = name.rfind('.')
    if dot < 0:
        return ''
    return name[dot:].lower()
