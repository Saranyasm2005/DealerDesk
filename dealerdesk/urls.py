from django.contrib import admin
from django.urls import include, path

from core.views import PortalView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include("core.urls")),
    path("", PortalView.as_view(), name="portal"),
]
