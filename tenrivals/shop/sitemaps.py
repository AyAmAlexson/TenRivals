"""XML sitemap entries for the storefront (used at /sitemap.xml)."""

from django.contrib.sitemaps import Sitemap
from django.db import DatabaseError
from django.urls import reverse

from .models import BlogPost, Product, ProductCollection


class ShopStaticSitemap(Sitemap):
    changefreq = 'weekly'
    priority = 0.85

    def items(self):
        return [
            'shop:index',
            'shop:stock',
            'shop:brands',
            'shop:order_for_me',
            'shop:blog_index',
            'shop:guide_racket_weight',
            'shop:guide_clay_shoes',
            'shop:product_search',
            'shop:cart',
            'shop:checkout',
            'shop:info_delivery',
            'shop:info_payment',
            'shop:info_returns',
            'shop:info_size_guide',
            'shop:info_contacts',
            'shop:legal_terms',
            'shop:legal_privacy',
            'shop:legal_cookies',
            'shop:legal_dmca',
            'shop:legal_company',
            'shop:legal_accessibility',
            'shop:legal_sitemap',
        ]

    def location(self, item):
        return reverse(item)


class ProductSitemap(Sitemap):
    changefreq = 'weekly'
    priority = 0.6

    def items(self):
        qs = Product.objects.filter(is_active=True).only('pk', 'updated_at').order_by('pk')[
            :50000
        ]
        try:
            return list(qs)
        except DatabaseError:
            return []

    def location(self, obj):
        return reverse('shop:product_detail', args=[obj.pk])

    def lastmod(self, obj):
        return obj.updated_at


class BlogPostSitemap(Sitemap):
    changefreq = 'monthly'
    priority = 0.65

    def items(self):
        qs = BlogPost.objects.filter(is_published=True).only(
            'slug', 'updated_at', 'published_at'
        ).order_by('-published_at', '-id')
        try:
            return list(qs)
        except DatabaseError:
            return []

    def location(self, obj):
        return reverse('shop:blog_post', kwargs={'slug': obj.slug})

    def lastmod(self, obj):
        return obj.updated_at


class ProductCollectionSitemap(Sitemap):
    changefreq = 'weekly'
    priority = 0.7

    def items(self):
        qs = ProductCollection.objects.filter(is_archived=False).only(
            'slug', 'updated_at'
        ).order_by('title', 'id')
        try:
            return list(qs)
        except DatabaseError:
            return []

    def lastmod(self, obj):
        return obj.updated_at

    def location(self, obj):
        return reverse('shop:collection_detail', kwargs={'slug': obj.slug})
