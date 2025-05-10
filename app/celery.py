import asyncio
import base64
from celery import Celery
from app.settings import settings

celery = Celery(
    "SRT_translator",
    broker=settings.REDIS_URL_LOCAL,
    backend=settings.REDIS_BACKEND_LOCAL,
)


@celery.task
def translator_SRT(srt_b64: str, filename: str):
    """
    Celery task koji prima base64 enkodiran srt file (kao list[str]),
    dekodira, i pokreće prevoditeljski workflow.
    """
    srt_bytes = base64.b64decode(srt_b64)
    decoded_str = srt_bytes.decode("utf-8")
    srt_lines = decoded_str.strip().split(
        "\n\n"
    )  # Pretpostavka: blokovi odvojeni praznom linijom

    async def _run():
        pass

    loop = asyncio.get_event_loop()
    loop.run_until_complete(_run())

    return f"Task for {filename} completed"
