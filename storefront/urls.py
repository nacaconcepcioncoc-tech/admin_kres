"""
URL configuration for the storefront project.
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from pages import views as page_views


urlpatterns = [
    path("admin/", admin.site.urls),
    path("login/", page_views.login_view, name="login"),
    path("", include("pages.urls")),
]


if settings.DEBUG:
    urlpatterns += static(
        settings.MEDIA_URL,
        document_root=settings.MEDIA_ROOT,
    )