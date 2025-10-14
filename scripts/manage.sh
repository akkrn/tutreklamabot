#!/bin/bash

# Скрипт для управления TutReklama Docker контейнерами

set -e

# Цвета для вывода
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Функция для вывода сообщений
log() {
    echo -e "${GREEN}[$(date +'%Y-%m-%d %H:%M:%S')]${NC} $1"
}

error() {
    echo -e "${RED}[ERROR]${NC} $1" >&2
}

warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

# Проверяем наличие docker compose
if ! docker compose version &> /dev/null; then
    error "docker compose не установлен. Установите Docker Compose."
    exit 1
fi

# Проверяем наличие .env файла
if [ ! -f ".env.docker" ]; then
    error "Файл .env.docker не найден. Скопируйте .env.example и настройте переменные."
    exit 1
fi

# Функция для запуска сервисов
start() {
    log "Запуск TutReklama сервисов..."

    # Копируем .env.docker в .env для docker compose
    cp .env.docker .env

    # Запускаем сервисы
    docker compose up -d

    log "Сервисы запущены!"
    info "Django Admin: http://localhost:8001/admin/"
    info "API: http://localhost:8001/"
    info "PostgreSQL: localhost:5433"
    info "Redis: localhost:6380"

    # Показываем статус
    docker compose ps
}

# Функция для остановки сервисов
stop() {
    log "Остановка TutReklama сервисов..."
    docker compose down
    log "Сервисы остановлены!"
}

# Функция для перезапуска сервисов
restart() {
    log "Перезапуск TutReklama сервисов..."
    docker compose restart
    log "Сервисы перезапущены!"
}

# Функция для просмотра логов
logs() {
    local service=${1:-""}
    if [ -n "$service" ]; then
        docker compose logs -f "$service"
    else
        docker compose logs -f
    fi
}

# Функция для выполнения команд Django
django() {
    docker compose exec web python manage.py "$@"
}

# Функция для создания суперпользователя
createsuperuser() {
    log "Создание суперпользователя..."
    docker compose exec web python manage.py createsuperuser
}

# Функция для применения миграций
migrate() {
    log "Применение миграций..."
    docker compose exec web python manage.py migrate
}

# Функция для сбора статических файлов
collectstatic() {
    log "Сбор статических файлов..."
    docker compose exec web python manage.py collectstatic --noinput
}

# Функция для обновления текстов
update_texts() {
    log "Обновление текстов..."
    docker compose exec web python manage.py update_texts
}

# Функция для очистки контейнеров и образов
clean() {
    warning "Это удалит все контейнеры, образы и volumes. Продолжить? (y/N)"
    read -r response
    if [[ "$response" =~ ^([yY][eE][sS]|[yY])$ ]]; then
        log "Очистка Docker ресурсов..."
        docker compose down -v --rmi all
        docker system prune -f
        log "Очистка завершена!"
    else
        info "Очистка отменена."
    fi
}

# Функция для показа статуса
status() {
    log "Статус сервисов:"
    docker compose ps

    echo ""
    log "Использование ресурсов:"
    docker stats --no-stream --format "table {{.Container}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}\t{{.BlockIO}}"
}

# Функция для показа помощи
help() {
    echo "TutReklama Docker Management Script"
    echo ""
    echo "Использование: $0 [КОМАНДА]"
    echo ""
    echo "Команды:"
    echo "  start              Запустить все сервисы"
    echo "  stop               Остановить все сервисы"
    echo "  restart            Перезапустить все сервисы"
    echo "  logs [service]     Показать логи (опционально для конкретного сервиса)"
    echo "  status             Показать статус сервисов"
    echo "  migrate            Применить миграции Django"
    echo "  collectstatic      Собрать статические файлы"
    echo "  createsuperuser    Создать суперпользователя"
    echo "  update_texts       Обновить тексты в БД"
    echo "  django [cmd]       Выполнить команду Django"
    echo "  clean              Очистить все Docker ресурсы"
    echo "  help               Показать эту справку"
    echo ""
    echo "Примеры:"
    echo "  $0 start"
    echo "  $0 logs web"
    echo "  $0 django shell"
    echo "  $0 migrate"
}

# Основная логика
case "${1:-help}" in
    start)
        start
        ;;
    stop)
        stop
        ;;
    restart)
        restart
        ;;
    logs)
        logs "$2"
        ;;
    status)
        status
        ;;
    migrate)
        migrate
        ;;
    collectstatic)
        collectstatic
        ;;
    createsuperuser)
        createsuperuser
        ;;
    update_texts)
        update_texts
        ;;
    django)
        shift
        django "$@"
        ;;
    clean)
        clean
        ;;
    help|--help|-h)
        help
        ;;
    *)
        error "Неизвестная команда: $1"
        help
        exit 1
        ;;
esac
