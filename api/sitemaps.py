from django.contrib.sitemaps import Sitemap
from django.urls import reverse

from .models import Course


class CourseSitemap(Sitemap):
    changefreq = "weekly"
    priority = 0.8

    def items(self):
        return Course.objects.filter(is_published=True)

    def lastmod(self, obj):
        return obj.updated_at

    def location(self, obj):
        return reverse('course_detail', args=[obj.slug])


class StaticViewSitemap(Sitemap):
    changefreq = "monthly"
    priority = 0.5

    def items(self):
        return ['home', 'courses', 'about', 'contact', 'faq', 'terms', 'privacy']

    def location(self, item):
        return reverse(item)
