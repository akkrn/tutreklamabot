from functools import partial

import structlog
from django.db import models


logger = structlog.getLogger(__name__)


class TruncatingCharField(models.CharField):
    """Текстовое поле, автоматически обрезающее задаваемые значения по max_length"""

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        if value is not None and self.max_length is not None:
            if len(value) > self.max_length:
                logger.warning(
                    "Обрезаем значение поля '%s' по допустимой длине (%d -> %d): '%s' -> '%s'",
                    self.name,
                    len(value),
                    self.max_length,
                    value,
                    value[: self.max_length],
                )
                return value[: self.max_length]
        return value
