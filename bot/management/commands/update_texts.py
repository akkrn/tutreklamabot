from django.core.management.base import BaseCommand
from django.db import transaction
import structlog

from bot.models import TextTemplate
from bot.default_texts import DEFAULT_TEXTS


logger = structlog.getLogger(__name__)


class Command(BaseCommand):
    help = ("Обновляет переводы: создает новые ключи, удаляет старые")

    def handle(self, *args, **options):
        logger.info("Начинаем обновление текстов...")

        # Создаем недостающие TextTemplate
        missing_created = self._create_missing_templates()

        # Удаляем устаревшие ключи
        old_removed = self._cleanup_old_templates()

        logger.info(
            f"Обновление завершено: "
            f"создано новых ключей: {missing_created}, "
            f"удалено старых ключей: {old_removed}, "
        )

    def _create_missing_templates(self):
        """Создает недостающие TextTemplate"""
        try:
            existing_template_keys = set(
                TextTemplate.objects.values_list("text_key", flat=True)
            )
            default_keys = set(DEFAULT_TEXTS.keys())
            missing_template_keys = default_keys - existing_template_keys

            if missing_template_keys:
                templates_to_create = []
                for key in missing_template_keys:
                    text = DEFAULT_TEXTS[key]
                    templates_to_create.append(
                        TextTemplate(text_key=key, default_text=text)
                    )

                TextTemplate.objects.bulk_create(
                    templates_to_create, ignore_conflicts=True
                )
                logger.info(f"Созданы TextTemplate для ключей: {missing_template_keys}")
                return len(missing_template_keys)
            else:
                logger.info("Нет новых ключей для создания")
                return 0
        except Exception as e:
            logger.error(f"Ошибка создания TextTemplate: {e}")
            return 0

    def _cleanup_old_templates(self):
        """Удаляет устаревшие ключи"""
        try:
            existing_template_keys = set(
                TextTemplate.objects.values_list("text_key", flat=True)
            )
            default_keys = set(DEFAULT_TEXTS.keys())
            extra_template_keys = existing_template_keys - default_keys

            if extra_template_keys:
                with transaction.atomic():
                    deleted_count, _ = TextTemplate.objects.filter(
                        text_key__in=extra_template_keys
                    ).delete()
                    logger.info(
                        f"Удалены устаревшие ключи: {extra_template_keys}, всего записей: {deleted_count}"
                    )
                return len(extra_template_keys)
            else:
                logger.info("Нет устаревших ключей для удаления")
                return 0
        except Exception as e:
            logger.error(f"Ошибка удаления старых ключей: {e}")
            return 0

