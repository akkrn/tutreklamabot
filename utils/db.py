from asgiref.sync import sync_to_async
from django.db import connection
from django.db import connections


def check_django_connection():
    """Закрыть отвалившиеся соединения к БД

    Имеет смысл для долгоживущих скриптов
    """
    for conn in connections.all():
        conn.close_if_unusable_or_obsolete()
    connection.cursor()


@sync_to_async
def acheck_django_connection():
    check_django_connection()
