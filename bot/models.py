import structlog
from django.db import models
from django_cryptography.fields import encrypt

from utils.models import TruncatingCharField


logger = structlog.getLogger(__name__)


class User(models.Model):
    """Пользователь (человек, вступивший в контакт с ботом)"""

    STATUS_ACTIVE = "active"
    STATUS_BANNED = "banned"
    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Активен"),
        (STATUS_BANNED, "Забанил"),
    ]

    tg_user_id = models.BigIntegerField("ID пользователя в Telegram", unique=True)
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

    def __str__(self):
        return f"{self.tg_user_id} {self.username or self.first_name}"

    class Meta:
        verbose_name = "Пользователь"
        verbose_name_plural = "Пользователи"


class UserBot(models.Model):
    """Аккаунт юзербота для подписки на каналы"""

    name = models.CharField(max_length=255, help_text="Название аккаунта для удобства")
    phone = models.CharField(max_length=32, unique=True, help_text="Номер телефона аккаунта")
    api_id = models.IntegerField()
    api_hash = models.CharField(max_length=128)
    session_file = models.CharField(max_length=255, help_text="Путь к .session (Telethon)")
    is_active = models.BooleanField(default=False, help_text="Авторизован и готов к работе")
    max_channels = models.IntegerField(default=500, help_text="Максимум каналов для этого аккаунта")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Телеграм аккаунт"
        verbose_name_plural = "Телеграм аккаунты"

    def __str__(self):
        return f"{self.name} ({self.phone})"

    @property
    def subscribed_channels_count(self):
        """Количество подписанных каналов"""
        return self.channel_subscriptions.count()

    @property
    def can_subscribe_more(self):
        """Может ли подписаться на еще каналы"""
        return self.subscribed_channels_count < self.max_channels
    
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

    channel = models.ForeignKey(Channel, on_delete=models.CASCADE, related_name="subscriptions")
    userbot = models.ForeignKey(
        UserBot,
        on_delete=models.CASCADE,
        related_name="channel_subscriptions"
    )
    is_subscribed = models.BooleanField(default=False, help_text="Успешно ли подписался")

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
    channel = models.ForeignKey(Channel, on_delete=models.CASCADE, related_name="news")
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
    text_key = models.CharField(max_length=100, unique=True, verbose_name="Ключ текста")
    default_text = models.TextField("Текст по умолчанию")
    updated = models.DateTimeField("Дата обновления", auto_now=True)

    class Meta:
        verbose_name = "Шаблон текста"
        verbose_name_plural = "Шаблоны текста"
