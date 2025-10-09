from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from shop.models import Product
import requests
from urllib.parse import urlparse


class Command(BaseCommand):
    help = "Re-download product images from stored attributes['source_images'] list, fill image_1..image_3"

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=None)

    def fetch_image_bytes(self, url: str) -> bytes | None:
        headers = {
            'User-Agent': 'Mozilla/5.0 (compatible; rehydrate/1.0)'
        }
        # Some CDNs require Referer; add for Tennis Warehouse Europe images
        if 'img.tenniswarehouse-europe.com' in url:
            headers['Referer'] = 'https://www.tenniswarehouse-europe.com/'
        try:
            resp = requests.get(url, timeout=20, headers=headers)
            resp.raise_for_status()
            if not resp.headers.get('Content-Type', '').startswith('image/'):
                return None
            return resp.content
        except Exception:
            return None

    def handle(self, *args, **options):
        qs = Product.objects.order_by('-updated_at')
        if options['limit']:
            qs = qs[: options['limit']]

        updated = 0
        for p in qs:
            candidate_urls: list[str] = []
            # 1) explicit list from attributes
            if isinstance(p.attributes, dict) and p.attributes.get('source_images'):
                candidate_urls.extend(p.attributes['source_images'])
            # 2) existing file URLs (may 404 on prod if file missing, we'll test)
            for f in (p.image_1, p.image_2, p.image_3):
                if f and getattr(f, 'url', None):
                    try:
                        candidate_urls.append(f.url)
                    except Exception:
                        pass
            # 3) derive by code from existing file names like CODE-1.jpg
            filenames = []
            for f in (p.image_1, p.image_2, p.image_3):
                if f and getattr(f, 'name', None):
                    filenames.append(f.name.split('/')[-1])
            code = None
            for fn in filenames:
                if '-' in fn:
                    code = fn.split('-')[0]
                    break
            if code:
                base = 'https://img.tenniswarehouse-europe.com/watermark/rs.php?path='
                candidate_urls.extend([f"{base}{code}-{i}.jpg&nw=1462" for i in range(1, 7)])
            if not urls:
                continue

            # clear existing files
            p.image_1 = None
            p.image_2 = None
            p.image_3 = None
            if hasattr(p, 'image_4'):
                p.image_4 = None
            if hasattr(p, 'image_5'):
                p.image_5 = None

            slot = 1
            seen = set()
            for u in candidate_urls:
                if u in seen:
                    continue
                seen.add(u)
                data = self.fetch_image_bytes(u)
                if not data:
                    continue
                filename = urlparse(u).path.split('/')[-1] or f'image_{slot}.jpg'
                getattr(p, f'image_{slot}').save(filename, ContentFile(data), save=False)
                slot += 1
                if slot > 5:
                    break
            p.save()
            updated += 1

        self.stdout.write(self.style.SUCCESS(f"Rehydrated images for {updated} products"))


