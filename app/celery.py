from celery import Celery

app = Celery(
    "SRT_translator",
    broker="redis://localhost:6379/0",  # ✅ obavezno Redis, ne AMQP
    backend="redis://localhost:6379/1",
)

app.conf.update(
    task_track_started=True,
    result_expires=3600,
)

app.autodiscover_tasks(["app"])  # ✅ da traži tasks.py u app/
