from celery import Celery
from celery.schedules import crontab
from app.core.config import settings

celery_app = Celery(
    "ms_scaner",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=["app.worker.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,          # подтверждаем задачу только после выполнения
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,  # честная очередь без накапливания
    task_default_retry_delay=5,
    task_max_retries=3,
)

# Периодические задачи (Celery Beat запускается встроенно в воркере: `-B`).
celery_app.conf.beat_schedule = {
    "cleanup-stale-processing": {
        "task": "cleanup_stale_processing",
        "schedule": crontab(minute=0),  # раз в час, в начале часа
    },
    # Инкрементальный добор ЭДО (лента Saby по курсору) для всех подключённых клиентов.
    "edo-auto-sync": {
        "task": "edo_auto_sync_all",
        "schedule": crontab(minute="*/30"),  # каждые 30 минут
    },
    # Ежедневное обновление снимка остатка ЧЗ (для актуальной сверки «не принятых»).
    "cz-snapshot-daily": {
        "task": "cz_snapshot_refresh_all",
        "schedule": crontab(hour=3, minute=30),  # раз в сутки, ночью (UTC)
    },
}
