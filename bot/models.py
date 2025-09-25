import structlog
from django.db import models
from django.db.models.query import QuerySet
from django.contrib.auth.models import User as DjangoUser
from django_cryptography.fields import encrypt


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
        return f"{self.telegram_user_id} {self.username or self.first_name}"

    class Meta:
        verbose_name = "Пользователь"
        verbose_name_plural = "Пользователи"
