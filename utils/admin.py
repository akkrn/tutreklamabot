from django.apps import apps
from django.contrib import admin
from django.contrib.admin.apps import AdminConfig


class CustomAdminSite(admin.AdminSite):
    """AdminSite, умеющий упорядочивать модели по приоритету"""

    def get_app_list(self, request, app_label=None):
        app_list = super().get_app_list(request, app_label=app_label)

        default_priority = 100
        for app in app_list:

            def _model_index(model):
                mclass = apps.get_model(app["app_label"], model["object_name"])
                madmin = self._registry.get(mclass)
                if not madmin:
                    return default_priority
                priority = getattr(madmin, "admin_priority", default_priority)
                return priority

            app["models"].sort(key=_model_index)
        return app_list


class CustomAdminConfig(AdminConfig):
    default_site = "utils.admin.CustomAdminSite"
