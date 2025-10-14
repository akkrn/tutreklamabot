import asyncio

import structlog
from django import forms
from django.contrib import admin
from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import redirect
from django.shortcuts import render
from django.urls import path
from django.urls import reverse
from django.utils.html import format_html

from bot.models import Channel
from bot.models import ChannelNews
from bot.models import ChannelSubscription
from bot.models import ChannelUser
from bot.models import TextTemplate
from bot.models import User
from bot.models import UserBot
from bot.services.userbot_auth import UserbotAuthService
from bot.services.userbot_manager import UserbotManagerService

logger = structlog.getLogger(__name__)


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = [
        "tg_user_id",
        "username",
        "first_name",
        "last_name",
        "status",
        "created",
    ]
    list_filter = ["status", "is_tg_premium", "created"]
    search_fields = ["tg_user_id", "username", "first_name", "last_name"]
    readonly_fields = [
        "tg_user_id",
        "tg_chat_id",
        "created",
        "contact_given",
        "status_changed_at",
    ]


class ChannelSubscriptionInline(admin.TabularInline):
    model = ChannelSubscription
    extra = 0


class UserBotAuthForm(forms.Form):
    """Форма для авторизации юзербота"""

    phone = forms.CharField(max_length=32, label="Номер телефона")
    code = forms.CharField(
        max_length=10, label="Код подтверждения", required=False
    )
    password = forms.CharField(
        max_length=100,
        label="Пароль 2FA",
        required=False,
        widget=forms.PasswordInput,
    )


@admin.register(UserBot)
class UserBotAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "phone",
        "status",
        "is_active",
        "subscribed_channels_count",
        "last_activity",
        "auth_actions",
    ]
    list_filter = ["status", "is_active", "created_at"]
    search_fields = ["name", "phone"]
    readonly_fields = [
        "last_activity",
        "last_error",
        "created_at",
        "updated_at",
    ]
    inlines = [ChannelSubscriptionInline]
    actions = [
        "authorize_userbot",
        "check_userbot_status",
        "start_userbot_action",
        "stop_userbot_action",
        "restart_userbot_action",
    ]

    fieldsets = (
        (
            "Основная информация",
            {"fields": ("name", "phone", "api_id", "api_hash")},
        ),
        (
            "Сессия",
            {
                "fields": ("session_file", "string_session"),
                "description": "Файл сессии создается автоматически. String session заполняется после авторизации.",
            },
        ),
        (
            "Статус",
            {"fields": ("status", "is_active", "last_activity", "last_error")},
        ),
        ("Настройки", {"fields": ("max_channels",)}),
        (
            "Системная информация",
            {"fields": ("created_at", "updated_at"), "classes": ("collapse",)},
        ),
    )

    def get_readonly_fields(self, request, obj=None):
        readonly = list(self.readonly_fields)
        readonly.append("session_file")  # Файл сессии создается автоматически
        if obj and obj.status == UserBot.STATUS_ACTIVE:
            readonly.extend(["api_id", "api_hash", "phone"])
        return readonly

    def auth_actions(self, obj):
        """Кнопки для авторизации и управления"""
        buttons = []

        if obj.status == UserBot.STATUS_INACTIVE:
            buttons.append(
                format_html(
                    '<a class="button" href="{}">Авторизовать</a>',
                    reverse("admin:bot_userbot_auth", args=[obj.pk]),
                )
            )
        elif obj.status == UserBot.STATUS_ERROR:
            buttons.append(
                format_html(
                    '<a class="button" href="{}">Переавторизовать</a>',
                    reverse("admin:bot_userbot_auth", args=[obj.pk]),
                )
            )
        elif obj.status == UserBot.STATUS_ACTIVE:
            buttons.append(
                format_html(
                    '<a class="button" href="{}">Проверить статус</a>',
                    reverse("admin:bot_userbot_check_status", args=[obj.pk]),
                )
            )

        # Добавляем кнопки управления для авторизованных юзерботов
        if obj.is_active:
            buttons.append(
                format_html(
                    '<a class="button" href="{}" style="background-color: #28a745; color: white;">Запустить</a>',
                    reverse("admin:bot_userbot_start", args=[obj.pk]),
                )
            )
            buttons.append(
                format_html(
                    '<a class="button" href="{}" style="background-color: #dc3545; color: white;">Остановить</a>',
                    reverse("admin:bot_userbot_stop", args=[obj.pk]),
                )
            )
            buttons.append(
                format_html(
                    '<a class="button" href="{}" style="background-color: #ffc107; color: black;">Перезапустить</a>',
                    reverse("admin:bot_userbot_restart", args=[obj.pk]),
                )
            )

        return format_html(" ".join(buttons)) if buttons else "-"

    auth_actions.short_description = "Действия"

    def start_userbot_action(self, request, queryset):
        """Действие для запуска юзерботов"""
        success_count = 0
        error_count = 0

        for userbot in queryset:
            result = asyncio.run(UserbotManagerService.start_userbot(userbot))
            if result["success"]:
                success_count += 1
            else:
                error_count += 1
                messages.error(
                    request, f"Ошибка запуска {userbot.name}: {result['error']}"
                )

        if success_count > 0:
            messages.success(
                request, f"Успешно запущено юзерботов: {success_count}"
            )
        if error_count > 0:
            messages.error(request, f"Ошибок при запуске: {error_count}")

    start_userbot_action.short_description = "Запустить выбранные юзерботы"

    def stop_userbot_action(self, request, queryset):
        """Действие для остановки юзерботов"""
        success_count = 0
        error_count = 0

        for userbot in queryset:
            result = asyncio.run(UserbotManagerService.stop_userbot(userbot))
            if result["success"]:
                success_count += 1
            else:
                error_count += 1
                messages.error(
                    request,
                    f"Ошибка остановки {userbot.name}: {result['error']}",
                )

        if success_count > 0:
            messages.success(
                request, f"Успешно остановлено юзерботов: {success_count}"
            )
        if error_count > 0:
            messages.error(request, f"Ошибок при остановке: {error_count}")

    stop_userbot_action.short_description = "Остановить выбранные юзерботы"

    def restart_userbot_action(self, request, queryset):
        """Действие для перезапуска юзерботов"""
        success_count = 0
        error_count = 0

        for userbot in queryset:
            result = asyncio.run(UserbotManagerService.restart_userbot(userbot))
            if result["success"]:
                success_count += 1
            else:
                error_count += 1
                messages.error(
                    request,
                    f"Ошибка перезапуска {userbot.name}: {result['error']}",
                )

        if success_count > 0:
            messages.success(
                request, f"Успешно перезапущено юзерботов: {success_count}"
            )
        if error_count > 0:
            messages.error(request, f"Ошибок при перезапуске: {error_count}")

    restart_userbot_action.short_description = (
        "Перезапустить выбранные юзерботы"
    )

    def get_urls(self):
        """Добавляем кастомные URL для авторизации"""
        urls = super().get_urls()
        custom_urls = [
            path(
                "<int:userbot_id>/auth/",
                self.admin_site.admin_view(self.userbot_auth_view),
                name="bot_userbot_auth",
            ),
            path(
                "<int:userbot_id>/check-status/",
                self.admin_site.admin_view(self.check_status_view),
                name="bot_userbot_check_status",
            ),
            path(
                "<int:userbot_id>/start/",
                self.admin_site.admin_view(self.start_userbot_view),
                name="bot_userbot_start",
            ),
            path(
                "<int:userbot_id>/stop/",
                self.admin_site.admin_view(self.stop_userbot_view),
                name="bot_userbot_stop",
            ),
            path(
                "<int:userbot_id>/restart/",
                self.admin_site.admin_view(self.restart_userbot_view),
                name="bot_userbot_restart",
            ),
        ]
        return custom_urls + urls

    def userbot_auth_view(self, request, userbot_id):
        """Страница авторизации юзербота"""
        try:
            userbot = UserBot.objects.get(id=userbot_id)
        except UserBot.DoesNotExist:
            messages.error(request, "Юзербот не найден")
            return redirect("admin:bot_userbot_changelist")

        if request.method == "POST":
            form = UserBotAuthForm(request.POST)
            if form.is_valid():
                phone = form.cleaned_data["phone"]
                code = form.cleaned_data.get("code")
                password = form.cleaned_data.get("password")

                # Обновляем номер телефона если изменился
                if userbot.phone != phone:
                    userbot.phone = phone
                    userbot.save()

                if "send_code" in request.POST:
                    # Отправляем код
                    result = asyncio.run(
                        UserbotAuthService.send_code_request(userbot)
                    )
                    if result["success"]:
                        messages.success(request, result["message"])
                        # Сохраняем phone_code_hash в сессии для следующего шага
                        request.session[f"phone_code_hash_{userbot_id}"] = (
                            result["phone_code_hash"]
                        )
                    else:
                        messages.error(request, result["error"])
                elif "verify_code" in request.POST and code:
                    # Проверяем код
                    phone_code_hash = request.session.get(
                        f"phone_code_hash_{userbot_id}"
                    )
                    if not phone_code_hash:
                        messages.error(
                            request,
                            "Не найден phone_code_hash. Начните авторизацию заново.",
                        )
                    else:
                        # Если пароль не введен, но требуется 2FA, показываем ошибку
                        if not password and request.session.get(
                            f"needs_password_{userbot_id}"
                        ):
                            messages.error(
                                request,
                                "Требуется пароль 2FA. Введите пароль и попробуйте снова.",
                            )
                        else:
                            logger.info(
                                f"Проверяем код для {userbot.phone}, пароль: {'указан' if password else 'не указан'}"
                            )
                            result = asyncio.run(
                                UserbotAuthService.verify_code(
                                    userbot, code, phone_code_hash, password
                                )
                            )
                            if result["success"]:
                                messages.success(request, result["message"])
                                # Очищаем сессию
                                if (
                                    f"phone_code_hash_{userbot_id}"
                                    in request.session
                                ):
                                    del request.session[
                                        f"phone_code_hash_{userbot_id}"
                                    ]
                                if (
                                    f"needs_password_{userbot_id}"
                                    in request.session
                                ):
                                    del request.session[
                                        f"needs_password_{userbot_id}"
                                    ]
                                return redirect(
                                    "admin:bot_userbot_change", userbot_id
                                )
                            else:
                                if result.get("needs_password"):
                                    messages.warning(request, result["error"])
                                    # Сохраняем флаг, что требуется пароль
                                    request.session[
                                        f"needs_password_{userbot_id}"
                                    ] = True
                                else:
                                    messages.error(request, result["error"])
        else:
            form = UserBotAuthForm(initial={"phone": userbot.phone})

        context = {
            "title": f"Авторизация юзербота: {userbot.name}",
            "form": form,
            "userbot": userbot,
            "opts": self.model._meta,
            "has_view_permission": True,
        }
        return render(request, "admin/bot/userbot/auth.html", context)

    def check_status_view(self, request, userbot_id):
        """Проверка статуса юзербота"""
        try:
            userbot = UserBot.objects.get(id=userbot_id)
        except UserBot.DoesNotExist:
            return JsonResponse({"error": "Юзербот не найден"})

        result = asyncio.run(UserbotAuthService.check_status(userbot))

        if result["success"]:
            if result["authorized"]:
                userbot.status = UserBot.STATUS_ACTIVE
                userbot.is_active = True
                userbot.last_error = ""
                userbot.save()
                messages.success(
                    request,
                    f"Юзербот активен. Пользователь: {result['user_info']['first_name']}",
                )
            else:
                userbot.status = UserBot.STATUS_ERROR
                userbot.last_error = "Не авторизован"
                userbot.save()
                messages.warning(request, "Юзербот не авторизован")
        else:
            userbot.status = UserBot.STATUS_ERROR
            userbot.last_error = result["error"]
            userbot.save()
            messages.error(request, f"Ошибка проверки: {result['error']}")

        return redirect("admin:bot_userbot_change", userbot_id)

    def start_userbot_view(self, request, userbot_id):
        """Запуск юзербота"""
        try:
            userbot = UserBot.objects.get(id=userbot_id)
        except UserBot.DoesNotExist:
            messages.error(request, "Юзербот не найден")
            return redirect("admin:bot_userbot_changelist")

        result = asyncio.run(UserbotManagerService.start_userbot(userbot))

        if result["success"]:
            messages.success(request, result["message"])
        else:
            messages.error(request, f"Ошибка запуска: {result['error']}")

        return redirect("admin:bot_userbot_change", userbot_id)

    def stop_userbot_view(self, request, userbot_id):
        """Остановка юзербота"""
        try:
            userbot = UserBot.objects.get(id=userbot_id)
        except UserBot.DoesNotExist:
            messages.error(request, "Юзербот не найден")
            return redirect("admin:bot_userbot_changelist")

        result = asyncio.run(UserbotManagerService.stop_userbot(userbot))

        if result["success"]:
            messages.success(request, result["message"])
        else:
            messages.error(request, f"Ошибка остановки: {result['error']}")

        return redirect("admin:bot_userbot_change", userbot_id)

    def restart_userbot_view(self, request, userbot_id):
        """Перезапуск юзербота"""
        try:
            userbot = UserBot.objects.get(id=userbot_id)
        except UserBot.DoesNotExist:
            messages.error(request, "Юзербот не найден")
            return redirect("admin:bot_userbot_changelist")

        result = asyncio.run(UserbotManagerService.restart_userbot(userbot))

        if result["success"]:
            messages.success(request, result["message"])
        else:
            messages.error(request, f"Ошибка перезапуска: {result['error']}")

        return redirect("admin:bot_userbot_change", userbot_id)

    def save_model(self, request, obj, form, change):
        """Сохранение модели"""
        if not change:  # Новый объект
            obj.status = UserBot.STATUS_INACTIVE
        super().save_model(request, obj, form, change)


@admin.register(Channel)
class ChannelAdmin(admin.ModelAdmin):
    list_display = [
        "title",
        "main_username",
        "telegram_id",
        "is_private",
        "created_at",
    ]
    list_filter = ["is_private", "created_at"]
    search_fields = ["title", "main_username", "telegram_id"]
    readonly_fields = ["telegram_id", "created_at", "updated_at"]


@admin.register(ChannelUser)
class ChannelUserAdmin(admin.ModelAdmin):
    list_display = ["user", "channel", "created_at"]
    list_filter = ["created_at"]
    search_fields = ["user__first_name", "user__username", "channel__title"]


@admin.register(ChannelSubscription)
class ChannelSubscriptionAdmin(admin.ModelAdmin):
    list_display = ["userbot", "channel", "is_subscribed", "created_at"]
    list_filter = ["is_subscribed", "created_at"]
    search_fields = ["userbot__name", "channel__title"]


@admin.register(ChannelNews)
class ChannelNewsAdmin(admin.ModelAdmin):
    list_display = ["channel", "message_id", "created_at"]
    list_filter = ["channel", "created_at"]
    search_fields = ["channel__title", "message"]
    readonly_fields = ["created_at"]


@admin.register(TextTemplate)
class TextTemplateAdmin(admin.ModelAdmin):
    list_display = ["text_key", "updated"]
    search_fields = ["text_key", "default_text"]
