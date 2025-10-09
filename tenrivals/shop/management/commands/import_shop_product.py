from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from shop.models import Product, ProductType, Shoe
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urljoin, parse_qs
import re
import json


class Command(BaseCommand):
    help = "Import a product from a URL. Usage: manage.py import_shop_product <url> --price 123.45 --type RACKET --brand Nike --sku ABC"

    def add_arguments(self, parser):
        parser.add_argument('url', type=str)
        parser.add_argument('--price', type=float, required=True)
        parser.add_argument('--type', type=str, choices=[c.value for c in ProductType], required=True)
        parser.add_argument('--brand', type=str, default=None)
        parser.add_argument('--sku', type=str, default=None)

    def fetch_html(self, url: str) -> str:
        resp = requests.get(
            url,
            timeout=20,
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            },
        )
        resp.raise_for_status()
        return resp.text

    def find_product_url_on_search_page(self, html: str, page_url: str) -> str | None:
        soup = BeautifulSoup(html, 'html.parser')
        # Prefer anchors that look like product cards linking to descpage*
        for a in soup.find_all('a', href=True):
            href = a['href']
            if 'descpage' in href:
                return urljoin(page_url, href)
        return None

    def parse(self, html: str, page_url: str):
        soup = BeautifulSoup(html, 'html.parser')
        # Title
        title_tag = soup.find('h1') or soup.find('title')
        title = title_tag.get_text(strip=True) if title_tag else None

        # Description: STRICT per earlier spec — take only from <h2><p>...</p></h2>
        short_description = None
        h2p = soup.select_one('h2 > p')
        if h2p:
            short_description = h2p.get_text(" ", strip=True)

        description = None
        desc_column = soup.select_one('div.desc_column')
        if desc_column:
            description = desc_column.get_text(" ", strip=True)

        # Images: STRICT — only from product carousel blocks
        images = []
        def pick_from_srcset(srcset: str):
            try:
                parts = [p.strip() for p in srcset.split(',') if p.strip()]
                if not parts:
                    return None
                # Choose the first candidate (usually largest width)
                url = parts[0].split()[0]
                return url.replace('&amp;', '&')
            except Exception:
                return None

        print(f"[import_shop_product] total <img>: {len(soup.find_all('img'))}")
        carousel_imgs = list(soup.select('div.prod_view-carousel-wrap-cell img.main_image'))
        print(f"[import_shop_product] carousel imgs (img.main_image): {len(carousel_imgs)}")
        # Fallback: picture > source inside carousel
        picture_sources = list(soup.select('div.prod_view-carousel-wrap-cell picture source'))
        print(f"[import_shop_product] carousel <source> in <picture>: {len(picture_sources)}")
        # Fallback: any img with itemprop=image inside carousel
        iprop_imgs = list(soup.select('div.prod_view-carousel-wrap-cell img[itemprop="image"]'))
        print(f"[import_shop_product] carousel img[itemprop=image]: {len(iprop_imgs)}")

        for idx_img, img in enumerate(carousel_imgs, start=1):
            print(f"[import_shop_product]  processing carousel img #{idx_img}")
            chosen = None
            srcset = img.get('srcset')
            if srcset:
                print(f"[import_shop_product]   srcset: {srcset[:200]}...")
                chosen = pick_from_srcset(srcset)
                print(f"[import_shop_product]   chosen from srcset: {chosen}")
            if not chosen:
                chosen = img.get('src')
                print(f"[import_shop_product]   fallback src: {chosen}")
            if not chosen:
                continue
            abs_src = urljoin(page_url, chosen)
            print(f"[import_shop_product]   abs src: {abs_src}")
            if abs_src not in images:
                images.append(abs_src)

        # If still empty, try <picture><source srcset="...">
        if not images and picture_sources:
            for idx_src, src in enumerate(picture_sources, start=1):
                srcset = src.get('srcset') or src.get('data-srcset')
                print(f"[import_shop_product]  processing <source> #{idx_src} srcset: {str(srcset)[:200]}...")
                if not srcset:
                    continue
                chosen = pick_from_srcset(srcset)
                print(f"[import_shop_product]   chosen from <source> srcset: {chosen}")
                if not chosen:
                    continue
                abs_src = urljoin(page_url, chosen)
                if abs_src not in images:
                    images.append(abs_src)

        # Fallback to itemprop=image imgs in carousel cells
        if not images and iprop_imgs:
            for idx_img, img in enumerate(iprop_imgs, start=1):
                chosen = img.get('src') or img.get('data-src')
                print(f"[import_shop_product]  itemprop image #{idx_img} src: {chosen}")
                if not chosen:
                    continue
                abs_src = urljoin(page_url, chosen)
                if abs_src not in images:
                    images.append(abs_src)

        # If still nothing, try parsing noscript within carousel cells
        if not images:
            cells = soup.select('div.prod_view-carousel-wrap-cell')
            print(f"[import_shop_product] carousel cells for noscript: {len(cells)}")
            for idx_cell, cell in enumerate(cells, start=1):
                ns = cell.find('noscript')
                if not ns:
                    continue
                ns_html = ns.string or ns.get_text()
                print(f"[import_shop_product]  noscript found in cell #{idx_cell}, length={len(ns_html) if ns_html else 0}")
                if not ns_html:
                    continue
                ns_soup = BeautifulSoup(ns_html, 'html.parser')
                for img in ns_soup.find_all('img'):
                    srcset = img.get('srcset')
                    chosen = pick_from_srcset(srcset) if srcset else (img.get('src') or img.get('data-src'))
                    print(f"[import_shop_product]   noscript img chosen: {chosen}")
                    if not chosen:
                        continue
                    abs_src = urljoin(page_url, chosen)
                    if abs_src not in images:
                        images.append(abs_src)

        # Final fallback: derive image URLs by product code from URL (e.g. BMJT2BA)
        if not images:
            m = re.search(r'-([A-Z0-9]+)-[A-Z]{2}\.html$', page_url)
            code = m.group(1) if m else None
            print(f"[import_shop_product] fallback by code from URL: code={code}")
            if not code:
                # try to sniff any rs.php?path=... occurrences in raw HTML
                paths = re.findall(r'rs\.php\?path=([A-Za-z0-9_-]+\.jpg)', str(soup))
                print(f"[import_shop_product] rs.php path candidates in HTML: {paths[:5]}")
                if paths:
                    code = paths[0].split('-')[0]
                    print(f"[import_shop_product] inferred code from path: {code}")
            if code:
                base = 'https://img.tenniswarehouse-europe.com/watermark/rs.php?path='
                for i in range(1, 7):
                    url_candidate = f"{base}{code}-{i}.jpg&nw=1462"
                    print(f"[import_shop_product]  candidate by code: {url_candidate}")
                    images.append(url_candidate)

        # Sizes (US): try explicit data attributes and visible labels containing "US"
        sizes_us = []
        # common data attributes
        for el in soup.select('[data-size-us], [data-us]'):
            val = el.get('data-size-us') or el.get('data-us')
            if val:
                label = f"US {val}" if 'US' not in val.upper() else val
                if label not in sizes_us:
                    sizes_us.append(label)
        # generic scan of options/buttons/spans containing US sizes
        for el in soup.select('option, button, span, a, div'):
            label = ' '.join([
                el.get('aria-label') or '',
                el.get_text(' ', strip=True) or ''
            ]).strip()
            if not label or 'US' not in label.upper():
                continue
            # capture US 9, US9.5 etc
            matches = re.findall(r'US\s*([0-9]+(?:\.[05])?)', label, flags=re.IGNORECASE)
            for m in matches:
                formatted = f'US {m}'
                if formatted not in sizes_us:
                    sizes_us.append(formatted)

        # Deduplicate while preserving order
        seen = set()
        deduped = []
        for u in images:
            if u and u not in seen:
                seen.add(u)
                deduped.append(u)
        images = deduped
        print(f"[import_shop_product] final images: {images}")

        # Key-value attributes from definition lists or tables
        attributes = {}
        for dl in soup.find_all('dl'):
            dts = dl.find_all('dt')
            dds = dl.find_all('dd')
            if len(dts) == len(dds) and len(dts) > 0:
                for dt, dd in zip(dts, dds):
                    key = dt.get_text(strip=True)
                    val = dd.get_text(" ", strip=True)
                    if key and val:
                        attributes[key] = val
        if not attributes:
            for table in soup.find_all('table'):
                for row in table.find_all('tr'):
                    cells = row.find_all(['td', 'th'])
                    if len(cells) == 2:
                        key = cells[0].get_text(strip=True)
                        val = cells[1].get_text(" ", strip=True)
                        if key and val:
                            attributes[key] = val

        return title, description, short_description, images, attributes, sizes_us

    def fetch_image_bytes(self, url: str, referer: str | None = None) -> bytes | None:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123 Safari/537.36',
            'Accept': 'image/avif,image/webp,image/apng,image/*,*/*;q=0.8',
        }
        if referer:
            headers['Referer'] = referer
        resp = requests.get(url, timeout=20, headers=headers)
        resp.raise_for_status()
        content_type = resp.headers.get('Content-Type', '')
        if not content_type.startswith('image/'):
            return None
        return resp.content

    @transaction.atomic
    def handle(self, *args, **options):
        url = options['url']
        price = options['price']
        type_code = options['type']
        brand = options['brand']
        sku = options['sku']

        html = self.fetch_html(url)
        title, description, short_description, images, attributes, sizes_us = self.parse(html, url)

        # If this looks like a search page (no carousel images), try to follow first product link
        if not images and ('search-tennis' in url or 'search' in url):
            maybe_product_url = self.find_product_url_on_search_page(html, url)
            if maybe_product_url:
                product_html = self.fetch_html(maybe_product_url)
                title, description, images, attributes, sizes_us = self.parse(product_html, maybe_product_url)
                url = maybe_product_url  # use as referer for images

        if not title:
            raise CommandError("Could not parse title from the page")

        # Try to update existing by name (case-insensitive)
        product = Product.objects.filter(name__iexact=title[:200]).first()

        SHOE_TYPES = {ProductType.MENS_SHOES, ProductType.WOMENS_SHOES, ProductType.JUNIOR_SHOES}
        if type_code in SHOE_TYPES:
            shoe_obj = None
            if product:
                try:
                    shoe_obj = product.shoe
                except Shoe.DoesNotExist:
                    product.delete()
                    shoe_obj = None
            if not shoe_obj:
                shoe_obj = Shoe(
                    type=type_code,
                    name=title[:200],
                    price=price,
                    brand=brand,
                    sku=sku,
                    short_description=short_description,
                    description=description or "",
                    attributes=attributes or {},
                )
            else:
                shoe_obj.type = type_code
                shoe_obj.price = price
                shoe_obj.brand = brand or shoe_obj.brand
                shoe_obj.sku = sku or shoe_obj.sku
                if description:
                    shoe_obj.description = description
                if attributes:
                    merged = dict(shoe_obj.attributes or {})
                    merged.update(attributes)
                    shoe_obj.attributes = merged
            if sizes_us:
                shoe_obj.sizes = sizes_us
            # clear images to reattach fresh
            shoe_obj.image_1 = None
            shoe_obj.image_2 = None
            shoe_obj.image_3 = None
            target_obj = shoe_obj
        else:
            if product:
                product.type = type_code
                product.price = price
                product.brand = brand or product.brand
                product.sku = sku or product.sku
                if description:
                    product.description = description
                if attributes:
                    merged = dict(product.attributes or {})
                    merged.update(attributes)
                    product.attributes = merged
                product.image_1 = None
                product.image_2 = None
                product.image_3 = None
                target_obj = product
            else:
                target_obj = Product(
                    type=type_code,
                    name=title[:200],
                    price=price,
                    brand=brand,
                    sku=sku,
                    description=description or "",
                    attributes=attributes or {},
                )

        # Attach up to 5 images
        for idx, img_url in enumerate(images[:5], start=1):
            try:
                data = self.fetch_image_bytes(img_url, referer=url)
                if data is None:
                    continue
                filename = urlparse(img_url).path.split('/')[-1] or f'image_{idx}.jpg'
                # Support image_4 and image_5
                field_name = f'image_{idx}'
                if not hasattr(target_obj, field_name):
                    break
                getattr(target_obj, field_name).save(filename, ContentFile(data), save=False)
            except Exception:
                continue
        target_obj.save()

        self.stdout.write(self.style.SUCCESS(f"Imported product #{target_obj.id}: {target_obj.name}"))

