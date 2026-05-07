from celery import Celery
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
