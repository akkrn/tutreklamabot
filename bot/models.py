from datetime import timedelta

import structlog
from django.db import models
from django.utils import timezone
from django_cryptography.fields import encrypt

from bot.constants import MAX_CHANNELS_PER_USER

logger = structlog.getLogger(__name__)


class User(models.Model):
    """Пользователь (человек, вступивший в контакт с ботом)"""

    STATUS_ACTIVE = "active"
    STATUS_BANNED = "banned"
    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Активен"),
        (STATUS_BANNED, "Забанил"),
    ]

    tg_user_id = models.BigIntegerField(
        "ID пользователя в Telegram", unique=True
    )
    tg_chat_id = models.BigIntegerField(
        "ID чата с пользователем в Telegram",
    )
    username = models.TextField(
        "Username", null=True, blank=True
    )  # не шифруем, чтобы было можно найти себя в списке. Не у всех пользователей Telegram есть username, поэтому null
    first_name = encrypt(models.TextField("Имя"))
    last_name = encrypt(
        models.TextField("Фамилия", null=True, blank=True)
    )  # не у всех пользователей Telegram есть last_name
    is_tg_premium = models.BooleanField("Премиум?", default=False)
    phone_number = encrypt(
        models.CharField(
            max_length=20,
            verbose_name="Номер телефона",
            null=True,
            blank=True,
            help_text="Появляется после того, как пользователь согласился предоставить свой контакт",
        )
    )
    language = models.CharField(
        max_length=10,
        verbose_name="Язык",
        help_text="Язык телеграма у пользователя",
        null=True,
        blank=True,
    )
    created = models.DateTimeField("Дата регистрации", auto_now_add=True)
    contact_given = models.DateTimeField(
        "Дата предоставления контакта", null=True, blank=True
    )

    referrer = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="referrals",
        verbose_name="Пригласивший пользователь",
        help_text="Устанавливается при переходе по реферальной ссылке",
    )
    status = models.CharField(
        verbose_name="Статус",
        choices=STATUS_CHOICES,
        default=STATUS_ACTIVE,
        max_length=10,
    )
    status_changed_at = models.DateTimeField(
        verbose_name="Дата смены статуса", null=True, blank=True
    )

    def get_display_name(self) -> str:
        """Возвращает @username, если он есть, иначе first_name."""
        if self.username:
            return f"@{self.username}"
        return self.first_name

    def get_current_tariff(self):
        """Возвращает текущий активный тариф пользователя"""
        return self.subscriptions.filter(
            status=UserSubscription.STATUS_ACTIVE, expires_at__gt=timezone.now()
        ).first()

    def get_channels_limit(self):
        """Возвращает лимит каналов для текущего тарифа"""
        current_tariff = self.get_current_tariff()
        if current_tariff:
            return current_tariff.tariff.channels_limit
        return MAX_CHANNELS_PER_USER

    def get_subscription_info(self):
        """Возвращает информацию о подписке"""
        current_tariff = self.get_current_tariff()
        if not current_tariff:
            return {
                "tariff_name": "Бесплатный",
                "channels_limit": MAX_CHANNELS_PER_USER,
                "days_remaining": 0,
                "is_active": False,
            }

        return {
            "tariff_name": current_tariff.tariff.name,
            "channels_limit": current_tariff.tariff.channels_limit,
            "days_remaining": current_tariff.days_remaining,
            "is_active": current_tariff.is_active,
            "expires_at": current_tariff.expires_at,
        }

    @property
    def subscribed_channels_count(self):
        """Количество подписанных каналов"""
        return self.channels.count()

    def __str__(self):
        return f"{self.tg_user_id} {self.username or self.first_name}"

    class Meta:
        verbose_name = "Пользователь"
        verbose_name_plural = "Пользователи"


class UserBot(models.Model):
    """Аккаунт юзербота для подписки на каналы"""

    STATUS_INACTIVE = "inactive"
    STATUS_AUTHORIZING = "authorizing"
    STATUS_ACTIVE = "active"
    STATUS_ERROR = "error"
    STATUS_CHOICES = [
        (STATUS_INACTIVE, "Неактивен"),
        (STATUS_AUTHORIZING, "Авторизуется"),
        (STATUS_ACTIVE, "Активен"),
        (STATUS_ERROR, "Ошибка"),
    ]

    name = models.CharField(
        max_length=255, help_text="Название аккаунта для удобства"
    )
    phone = models.CharField(
        max_length=32, unique=True, help_text="Номер телефона аккаунта"
    )
    api_id = models.IntegerField()
    api_hash = models.CharField(max_length=128)
    session_file = models.CharField(
        max_length=255, help_text="Путь к .session (Telethon)", default=""
    )
    string_session = encrypt(
        models.TextField(
            blank=True, null=True, help_text="String session для авторизации"
        )
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_INACTIVE,
        help_text="Статус юзербота",
    )
    is_active = models.BooleanField(
        default=False, help_text="Авторизован и готов к работе"
    )
    max_channels = models.IntegerField(
        default=500, help_text="Максимум каналов для этого аккаунта"
    )
    last_error = models.TextField(blank=True, help_text="Последняя ошибка")
    last_activity = models.DateTimeField(
        null=True, blank=True, help_text="Последняя активность"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Телеграм аккаунт"
        verbose_name_plural = "Телеграм аккаунты"

    def __str__(self):
        return f"{self.name} ({self.phone}) - {self.get_status_display()}"

    @property
    def subscribed_channels_count(self):
        """Количество подписанных каналов"""
        return self.channel_subscriptions.count()

    @property
    def can_subscribe_more(self):
        """Может ли подписаться на еще каналы"""
        return self.subscribed_channels_count < self.max_channels

    def get_session_path(self):
        """Возвращает путь к файлу сессии"""
        if self.session_file:
            return self.session_file
        if self.id:
            return f"userbot/sessions/{self.id}_{self.phone.replace('+', '')}.session"
        return f"userbot/sessions/temp_{self.phone.replace('+', '')}.session"

    def save(self, *args, **kwargs):
        """Автоматически устанавливает путь к сессии при сохранении"""
        if not self.pk:
            super().save(*args, **kwargs)

        if not self.session_file or "temp_" in self.session_file:
            self.session_file = f"userbot/sessions/{self.id}_{self.phone.replace('+', '')}.session"

        super().save(*args, **kwargs)


class Channel(models.Model):
    telegram_id = models.BigIntegerField(unique=True, db_index=True)
    title = models.TextField()
    main_username = models.TextField(null=True, blank=True)
    link_subscription = models.TextField(null=True, blank=True)
    is_private = models.BooleanField(default=False)

    users = models.ManyToManyField(
        User,
        through="ChannelUser",
        related_name="channels",
        blank=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Канал"
        verbose_name_plural = "Каналы"

    def __str__(self):
        return f"{self.title} ({self.telegram_id})"


class ChannelUser(models.Model):
    """Связь пользователь-канал (какие каналы пользователь хочет отслеживать)"""

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    channel = models.ForeignKey(Channel, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Канал пользователя"
        verbose_name_plural = "Каналы пользователя"
        unique_together = (("user", "channel"),)


class ChannelSubscription(models.Model):
    """Связь канал-юзербот (какой юзербот подписан на какой канал)"""

    channel = models.ForeignKey(
        Channel, on_delete=models.CASCADE, related_name="subscriptions"
    )
    userbot = models.ForeignKey(
        UserBot, on_delete=models.CASCADE, related_name="channel_subscriptions"
    )
    is_subscribed = models.BooleanField(
        default=False, help_text="Успешно ли подписался"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Подписка юзербота"
        verbose_name_plural = "Подписки юзерботов"
        unique_together = (("channel", "userbot"),)
        indexes = [
            models.Index(fields=["channel", "is_subscribed"]),
            models.Index(fields=["userbot", "is_subscribed"]),
        ]

    def __str__(self):
        status = "✓" if self.is_subscribed else "✗"
        return f"{status} {self.userbot.name} → {self.channel.title}"


class ChannelNews(models.Model):
    channel = models.ForeignKey(
        Channel, on_delete=models.CASCADE, related_name="news"
    )
    message_id = models.BigIntegerField()
    message = models.TextField(default="")
    url = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Сообщение из канала"
        verbose_name_plural = "Сообщения из каналов"
        unique_together = (("channel", "message_id"),)
        indexes = [
            models.Index(fields=["channel"]),
            models.Index(fields=["-created_at", "channel"]),
        ]


class TextTemplate(models.Model):
    text_key = models.CharField(
        max_length=100, unique=True, verbose_name="Ключ текста"
    )
    default_text = models.TextField("Текст по умолчанию")
    updated = models.DateTimeField("Дата обновления", auto_now=True)

    class Meta:
        verbose_name = "Шаблон текста"
        verbose_name_plural = "Шаблоны текста"


class Tariff(models.Model):
    """Тарифный план"""

    name = models.CharField(
        max_length=100,
        verbose_name="Название тарифа",
        help_text="Например: Базовый, Премиум, VIP",
    )
    price = models.PositiveIntegerField(
        verbose_name="Цена в рублях", help_text="Цена в рублях"
    )
    channels_limit = models.PositiveIntegerField(
        verbose_name="Лимит каналов",
        help_text="Максимальное количество каналов для отслеживания",
    )
    duration_days = models.PositiveIntegerField(
        verbose_name="Длительность в днях",
        help_text="Сколько дней действует тариф (30, 90, 180 и т.д.)",
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name="Активен",
        help_text="Доступен ли тариф для покупки",
    )
    description = models.TextField(
        blank=True,
        verbose_name="Описание",
        help_text="Дополнительное описание тарифа",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Тариф"
        verbose_name_plural = "Тарифы"
        ordering = ["price"]

    def __str__(self):
        return f"{self.name} - {self.get_price_display()}"

    def get_price_display(self):
        """Возвращает цену в рублях"""
        return f"{self.price} ₽"


class UserSubscription(models.Model):
    """Подписка пользователя на тариф"""

    STATUS_ACTIVE = "active"
    STATUS_EXPIRED = "expired"
    STATUS_CANCELLED = "cancelled"
    STATUS_UNPAID = "unpaid"
    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Активна"),
        (STATUS_EXPIRED, "Истекла"),
        (STATUS_CANCELLED, "Отменена"),
        (STATUS_UNPAID, "Неоплачена"),
    ]

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="subscriptions",
        verbose_name="Пользователь",
    )
    tariff = models.ForeignKey(
        Tariff,
        on_delete=models.CASCADE,
        related_name="user_subscriptions",
        verbose_name="Тариф",
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_ACTIVE,
        verbose_name="Статус подписки",
    )
    started_at = models.DateTimeField(
        verbose_name="Дата начала подписки", auto_now_add=True
    )
    expires_at = models.DateTimeField(verbose_name="Дата окончания подписки")
    robokassa_invoice_id = models.PositiveIntegerField(
        unique=True,
        verbose_name="ID инвойса Robokassa",
        help_text="Уникальный идентификатор инвойса в системе Robokassa (1-2147483647)",
    )
    previous_subscription = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="recurring_subscriptions",
        verbose_name="Предыдущая подписка",
        help_text="Ссылка на предыдущую подписку для рекуррентных платежей",
    )
    is_recurring_enabled = models.BooleanField(
        default=True,
        verbose_name="Автопродление включено",
        help_text="Включена ли автоплатеж для данной подписки",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Подписка пользователя"
        verbose_name_plural = "Подписки пользователей"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "status"]),
            models.Index(fields=["expires_at"]),
        ]

    def __str__(self):
        return f"{self.user} - {self.tariff.name} ({self.get_status_display()})"

    @property
    def is_active(self):
        """Активна ли подписка"""
        if not self.expires_at:
            return False
        return (
            self.status == self.STATUS_ACTIVE
            and self.expires_at > timezone.now()
        )

    @property
    def days_remaining(self):
        """Сколько дней осталось до окончания"""
        if not self.expires_at:
            return 0
        if self.expires_at <= timezone.now():
            return 0
        return (self.expires_at - timezone.now()).days

    def save(self, *args, **kwargs):
        """Автоматически устанавливает дату окончания при создании"""
        if not self.pk and not self.expires_at:
            self.expires_at = timezone.now() + timedelta(
                days=self.tariff.duration_days
            )
        super().save(*args, **kwargs)
