"""Management команда для обработки рекуррентных платежей"""

import structlog
from django.core.management.base import BaseCommand

from bot.tasks import process_recurring_payments_task

logger = structlog.getLogger(__name__)


class Command(BaseCommand):
    """
    Обработка рекуррентных платежей.

    Находит подписки, которые истекают в ближайшие дни,
    и создает рекуррентные платежи для их автоматического продления.
    """

    help = "Обрабатывает рекуррентные платежи для подписок, которые истекают"

    def add_arguments(self, parser):
        parser.add_argument(
            "--days-before-expiry",
            type=int,
            default=3,
            help="Сколько дней до истечения подписки создавать рекуррентный платеж",
        )
        parser.add_argument(
            "--user-id",
            type=int,
            default=278677011,
            help="ID пользователя в Telegram для обработки (по умолчанию 278677011)",
        )

    def handle(self, *args, **options):
        days_before_expiry = options.get("days_before_expiry", 3)
        user_id = options.get("user_id", 278677011)

        logger.info(
            "Запуск обработки рекуррентных платежей",
            days_before_expiry=days_before_expiry,
            user_id=user_id,
        )

        task = process_recurring_payments_task.delay(
            days_before_expiry, user_id
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Задача отправлена в Celery (task_id: {task.id}, user_id: {user_id})"
            )
        )
        self.stdout.write(
            f"Для проверки статуса: celery -A core result {task.id}"
        )
