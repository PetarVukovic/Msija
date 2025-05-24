from celery import shared_task
import asyncio
import httpx
import logging
import os
import redis
import time
from typing import List, Dict, Any
import traceback

# Import iz main.py - pretpostavljajući da je main.py ažuriran s novim funkcijama
from app.main import LLM, save_to_disk, send_to_n8n, translate_chunk_with_backoff
from app.settings import settings as sett

# Poboljšani logging
log = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

# Redis klijent za praćenje napretka
redis_client = redis.Redis.from_url(
    os.getenv("REDIS_URL_LOCAL", "redis://localhost:6379/0")
)


@shared_task(bind=True, name="translate_language_task", max_retries=3)
def translate_language_task(
    self, eng_blocks, lang: str, filename: str, temp_dir: str, job_id: str
):
    """
    Zadatak za prevođenje SRT datoteke na određeni jezik

    Args:
        eng_blocks: Lista blokova za prevođenje
        lang: Ciljni jezik
        filename: Ime izvorne datoteke
        temp_dir: Direktorij za privremeno spremanje prijevoda
        job_id: Jedinstveni identifikator zadatka
    """
    task_id = self.request.id
    progress_key = f"translation:{job_id}:{lang}"

    # Označi zadatak kao započet
    redis_client.hset(progress_key, "status", "in_progress")
    log.info(
        f"Započinjem zadatak prijevoda {task_id} za jezik {lang}, datoteka {filename}"
    )

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(
            _translate_and_send(eng_blocks, lang, filename, temp_dir, job_id)
        )
    except Exception as exc:
        # Zabilježi grešku
        error_msg = str(exc)
        trace = traceback.format_exc()
        log.error(f"Greška u prijevodu za {lang}: {error_msg}\n{trace}")

        # Ažuriraj status u Redisu
        redis_client.hset(
            progress_key,
            mapping={"status": "failed", "error": error_msg, "end_time": time.time()},
        )

        # Pokušaj ponovno izvršiti zadatak s postupnim odgađanjem
        retry_delay = 60 * (
            2 ** (self.request.retries)
        )  # Eksponencijalno povećanje: 60s, 120s, 240s...
        raise self.retry(exc=exc, countdown=retry_delay)


async def _translate_and_send(
    blocks, lang: str, filename: str, temp_dir: str, job_id: str
):
    """
    Prevodi blokove na specifični jezik i šalje rezultat na N8N webhook
    """
    start_time = time.time()
    progress_key = f"translation:{job_id}:{lang}"

    # Postavi početne vrijednosti
    redis_client.hset(
        progress_key,
        mapping={
            "status": "in_progress",
            "start_time": start_time,
            "total_blocks": len(blocks),
            "completed_blocks": 0,
            "percent_complete": 0,
        },
    )

    try:
        # Inicijaliziraj LLM
        llm = LLM(sett.llm_model)

        # Prevedi s optimiziranom funkcijom s postupnim odgađanjem
        log.info(f"Prevodim {len(blocks)} blokova na {lang}")
        translated = await translate_chunk_with_backoff(blocks, lang, llm, job_id)

        # Spremi rezultat na disk
        output_path = save_to_disk(temp_dir, lang, filename, translated)

        # Označi kao završeno
        end_time = time.time()
        duration = end_time - start_time
        redis_client.hset(
            progress_key,
            mapping={
                "status": "completed",
                "completed_blocks": len(blocks),
                "percent_complete": 100,
                "end_time": end_time,
                "duration_seconds": int(duration),
                "output_path": output_path,
            },
        )

        # Ažuriraj brojač završenih jezika za cijeli posao
        redis_client.hincrby(f"job:{job_id}", "completed_languages", 1)

        # Provjeri jesu li svi jezici završeni
        completed = int(redis_client.hget(f"job:{job_id}", "completed_languages") or 0)
        queued = int(redis_client.hget(f"job:{job_id}", "queued_languages") or 0)

        if completed >= queued:
            redis_client.hset(f"job:{job_id}", "status", "completed")
            log.info(f"Svi jezici za posao {job_id} su završeni!")

            # Oslobodi kvotu za korisnika ako postoji
            user_id = redis_client.hget(f"job:{job_id}", "user_id")
            if user_id:
                redis_client.decr(f"user_active_jobs:{user_id.decode('utf-8')}")

        # Pošalji na N8N webhook
        log.info(f"Šaljem prijevod na {lang} za {filename} na N8N webhook")
        async with httpx.AsyncClient() as client:
            webhook_status = await send_to_n8n(
                client, filename, lang, translated, job_id
            )

            # Zabilježi status webhook poziva
            if webhook_status >= 200 and webhook_status < 300:
                redis_client.hset(progress_key, "webhook_status", "success")
            else:
                redis_client.hset(
                    progress_key, "webhook_status", f"failed:{webhook_status}"
                )

        # Vrati rezultat
        return {
            "lang": lang,
            "status": "completed",
            "blocks_count": len(blocks),
            "duration_seconds": int(duration),
            "output_path": output_path,
        }

    except Exception as e:
        error_msg = str(e)
        log.error(f"Greška u prevođenju na {lang}: {error_msg}")

        # Ažuriraj status
        redis_client.hset(
            progress_key,
            mapping={"status": "error", "error": error_msg, "end_time": time.time()},
        )

        # Ponovno podigni iznimku za Celery retry mehanizam
        raise


@shared_task(name="translation_healthcheck")
def translation_healthcheck():
    """
    Zadatak za provjeru stanja dugotrajnih prijevoda i ponovno pokretanje neuspjelih
    """
    now = time.time()
    stalled_timeout = 1800  # 30 minuta bez napretka

    # Pronađi sve aktivne poslove
    active_jobs = []
    for key in redis_client.keys("job:*"):
        job_id = key.decode("utf-8").split(":")[1]
        status = redis_client.hget(key, "status")

        if status and status.decode("utf-8") == "in_progress":
            active_jobs.append(job_id)

    log.info(f"Provjera zdravlja za {len(active_jobs)} aktivnih poslova")

    # Provjeri svaki aktivni posao
    for job_id in active_jobs:
        # Provjeri sve jezike u tom poslu
        for lang_key in redis_client.keys(f"translation:{job_id}:*"):
            lang = lang_key.decode("utf-8").split(":")[-1]
            status = redis_client.hget(lang_key, "status")

            if status and status.decode("utf-8") == "in_progress":
                # Provjeri vrijeme zadnje aktivnosti
                last_update = redis_client.hget(lang_key, "last_update")

                if last_update:
                    last_update_time = float(last_update.decode("utf-8"))
                    if now - last_update_time > stalled_timeout:
                        log.warning(
                            f"Zastao prijevod za {lang} u poslu {job_id}, ponovno pokretanje"
                        )

                        # Ponovno pokreni zadatak
                        # Napomena: Ovo je pojednostavljena verzija, trebali biste dohvatiti originalne parametre
                        redis_client.hset(lang_key, "status", "queued")

                        # Ovdje biste trebali implementirati mehanizam za ponovno pokretanje
                        # translate_language_task.apply_async(...)

    return {"checked_jobs": len(active_jobs)}


@shared_task(name="cleanup_stale_jobs")
def cleanup_stale_jobs(max_age_hours=72):
    """Očisti stare poslove iz Redisa i privremene direktorije"""
    now = time.time()
    max_age_seconds = max_age_hours * 3600
    deleted_count = 0

    # Pronađi stare poslove
    for key in redis_client.keys("job:*"):
        job_id = key.decode("utf-8").split(":")[1]
        start_time = redis_client.hget(key, "start_time")

        if start_time:
            job_age = now - float(start_time.decode("utf-8"))

            if job_age > max_age_seconds:
                # Obriši podatke o poslu
                for related_key in redis_client.keys(f"*{job_id}*"):
                    redis_client.delete(related_key)

                # Obriši direktorij ako postoji
                import glob
                import shutil

                for dir_path in glob.glob(f"temp_translations_{job_id}*"):
                    if os.path.isdir(dir_path):
                        try:
                            shutil.rmtree(dir_path)
                            log.info(f"Obrisan stari privremeni direktorij: {dir_path}")
                        except Exception as e:
                            log.error(
                                f"Greška pri brisanju direktorija {dir_path}: {e}"
                            )

                deleted_count += 1
                log.info(f"Očišćen stari posao: {job_id}")

    return {"cleaned_jobs": deleted_count}
