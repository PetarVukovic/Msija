from fastapi import (
    FastAPI,
    File,
    UploadFile,
    HTTPException,
    BackgroundTasks,
    Depends,
    Header,
)
from fastapi.middleware.cors import CORSMiddleware
import base64
import logging
import redis
import uuid
from celery import Celery
import os
import asyncio
import httpx
import json
import time
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import SystemMessage, HumanMessage
from typing import List, Dict, Any, Optional
from app.settings import settings as sett
from app.tasks import translate_language_task

# Poboljšani logging setup
log = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

app = FastAPI(title="SRT Translation Service", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

celery = Celery(
    "SRT_translator",
    broker=os.getenv("REDIS_URL_LOCAL", "redis://localhost:6379/0"),
    backend=os.getenv("REDIS_BACKEND_LOCAL", "redis://localhost:6379/1"),
)

# Redis klijent za praćenje napretka
redis_client = redis.Redis.from_url(
    os.getenv("REDIS_URL_LOCAL", "redis://localhost:6379/0")
)

N8N_WEBHOOK_URL = os.getenv("N8N_WEBHOOK_URL")
TARGET_LANGUAGES = sett.target_languages

# Maksimalna veličina datoteke (10MB)
MAX_FILE_SIZE = 10 * 1024 * 1024


class LLM:
    def __init__(self, model_name: str):
        self.model = ChatGoogleGenerativeAI(model=model_name)

    async def translate_srt_chunk(self, chunk: str, lang: str) -> str:
        sys = SystemMessage(
            content=f"Translate this SRT chunk to {lang}. Keep formatting exactly the same."
        )
        user = HumanMessage(content=chunk)

        try:
            response = await self.model.ainvoke([sys, user])
            return response.content
        except Exception as e:
            log.error(f"Translation error: {str(e)}")
            raise


def _generate_safe_filename(original_filename: str) -> str:
    base = os.path.splitext(original_filename)[0]
    safe_base = "".join(c if c.isalnum() or c in ("_", "-") else "_" for c in base)
    return f"{safe_base}_translated.srt"


def save_to_disk(output_dir: str, lang: str, original_filename: str, content: str):
    os.makedirs(output_dir, exist_ok=True)
    safe_filename = _generate_safe_filename(original_filename)
    path = os.path.join(output_dir, f"{lang.lower()}_{safe_filename}")

    with open(path, "w", encoding="utf-8-sig") as f:
        f.write(content)

    log.info(f"Saved translation to {path}")
    return path


async def parse_srt_to_blocks(srt_bytes: bytes) -> List[str]:
    try:
        text = srt_bytes.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = srt_bytes.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = srt_bytes.decode("latin1")

    blocks = [
        b.strip() for b in text.replace("\r\n", "\n").strip().split("\n\n") if b.strip()
    ]
    log.info(f"Parsed {len(blocks)} SRT blocks")
    return blocks


async def translate_chunk_with_backoff(
    blocks: List[str], lang: str, llm: LLM, job_id: str
) -> str:
    """Translate chunks with adaptive backoff for rate limiting"""
    result = []
    progress_key = f"translation:{job_id}:{lang}"
    total_blocks = len(blocks)
    completed_blocks = 0

    # Update progress
    redis_client.hset(
        progress_key,
        mapping={"total_blocks": total_blocks, "completed_blocks": completed_blocks},
    )

    for i in range(0, len(blocks), sett.blocks_per_chunk):
        chunk_blocks = blocks[i : i + sett.blocks_per_chunk]
        chunk = "\n\n".join(chunk_blocks)

        # Implement adaptive backoff
        max_retries = 5
        delay = sett.api_call_delay

        for attempt in range(max_retries):
            try:
                translated = await llm.translate_srt_chunk(chunk, lang)
                result.append(translated.strip())

                # Update progress
                completed_blocks += len(chunk_blocks)
                redis_client.hset(progress_key, "completed_blocks", completed_blocks)
                redis_client.hset(
                    progress_key,
                    "percent_complete",
                    int((completed_blocks / total_blocks) * 100),
                )

                # Successful translation, use normal delay
                await asyncio.sleep(delay)
                break
            except Exception as e:
                log.warning(
                    f"Translation attempt {attempt+1} failed for {lang}: {str(e)}"
                )
                if "quota" in str(e).lower() or "rate" in str(e).lower():
                    # Rate limit hit, use exponential backoff
                    await asyncio.sleep(delay)
                    delay *= 2  # Exponential backoff
                    if attempt == max_retries - 1:
                        log.error(
                            f"Failed to translate chunk after {max_retries} attempts"
                        )
                        raise
                else:
                    # Other error, re-raise
                    raise

    return "\n\n".join(result)


async def send_to_n8n(
    client: httpx.AsyncClient, filename: str, lang: str, content: str, job_id: str
) -> int:
    if not N8N_WEBHOOK_URL:
        log.warning("N8N_WEBHOOK_URL not set. Skipping webhook notification.")
        return 503

    try:
        resp = await client.post(
            N8N_WEBHOOK_URL,
            json={
                "job_id": job_id,
                "filename": filename,
                "language": lang,
                "translated_srt": content,
            },
            timeout=sett.n8n_timeout,
        )
        log.info(f"N8N webhook response for {lang}: {resp.status_code}")
        return resp.status_code
    except Exception as e:
        log.error(f"N8N webhook error: {str(e)}")
        return 500


@celery.task(name="process_srt_task", bind=True)
def process_srt_task(self, srt_b64: str, filename: str, user_id: str = None):
    job_id = str(uuid.uuid4())

    # Initialize job in Redis
    job_key = f"job:{job_id}"
    redis_client.hset(
        job_key,
        mapping={
            "status": "processing",
            "filename": filename,
            "user_id": user_id or "anonymous",
            "start_time": time.time(),
            "target_languages": json.dumps(TARGET_LANGUAGES),
        },
    )

    log.info(f"Starting job {job_id} for file {filename}")

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    return loop.run_until_complete(_run_main_translation(srt_b64, filename, job_id))


async def _run_main_translation(srt_b64: str, filename: str, job_id: str):
    try:
        srt_bytes = base64.b64decode(srt_b64)
        blocks = await parse_srt_to_blocks(srt_bytes)

        if not blocks:
            log.error(f"No blocks found in SRT file {filename}")
            redis_client.hset(f"job:{job_id}", "status", "error:no_blocks")
            return {"status": "error", "message": "No valid SRT blocks found in file"}

        llm = LLM(sett.llm_model)

        # Create unique temp directory with job ID
        temp_dir = f"temp_translations_{job_id}_{filename.replace('.', '_')}"
        os.makedirs(temp_dir, exist_ok=True)

        # Set progress for English translation
        progress_key = f"translation:{job_id}:English"
        redis_client.hset(
            progress_key,
            mapping={
                "status": "in_progress",
                "total_blocks": len(blocks),
                "completed_blocks": 0,
                "start_time": time.time(),
            },
        )

        # Translate to English first
        english_srt = await translate_chunk_with_backoff(blocks, "English", llm, job_id)
        save_to_disk(temp_dir, "English", filename, english_srt)

        # Mark English as completed
        redis_client.hset(
            progress_key,
            mapping={
                "status": "completed",
                "completed_blocks": len(blocks),
                "percent_complete": 100,
                "end_time": time.time(),
            },
        )

        # Send to N8N
        async with httpx.AsyncClient() as client:
            await send_to_n8n(client, filename, "English", english_srt, job_id)

        # Parse English blocks to use as source for other languages
        eng_blocks = await parse_srt_to_blocks(english_srt.encode("utf-8"))

        # Launch tasks for each target language
        for lang in TARGET_LANGUAGES:
            if lang.lower() == "english":
                continue

            # Initialize progress for this language
            lang_progress_key = f"translation:{job_id}:{lang}"
            redis_client.hset(
                lang_progress_key,
                mapping={
                    "status": "queued",
                    "total_blocks": len(eng_blocks),
                    "completed_blocks": 0,
                    "start_time": time.time(),
                },
            )

            # Queue the translation task
            translate_language_task.delay(eng_blocks, lang, filename, temp_dir, job_id)

        # Update job status
        redis_client.hset(
            f"job:{job_id}",
            mapping={
                "status": "in_progress",
                "english_completed": True,
                "queued_languages": len(TARGET_LANGUAGES)
                - (1 if "english" in [l.lower() for l in TARGET_LANGUAGES] else 0),
            },
        )

        return {
            "status": "Queued translations for all target languages",
            "job_id": job_id,
            "english_translation_completed": True,
        }

    except Exception as e:
        log.error(f"Translation error for job {job_id}: {str(e)}")
        redis_client.hset(f"job:{job_id}", "status", f"error:{str(e)}")
        return {"status": "error", "message": str(e)}


@app.post("/uploadSrt")
async def upload_srt_endpoint(
    file: UploadFile = File(...),
    user_id: Optional[str] = None,
    x_api_key: Optional[str] = Header(None),
):
    # Validate API key if required
    if sett.require_api_key and x_api_key != sett.api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")

    # Validate file extension
    if not file.filename.lower().endswith(".srt"):
        raise HTTPException(
            status_code=400, detail="Invalid file type. Only .srt files are accepted"
        )

    # Read file content
    file_content = await file.read()

    # Validate file size
    if len(file_content) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"File too large. Maximum size is {MAX_FILE_SIZE // (1024*1024)}MB",
        )

    # Validate file content
    if not file_content:
        raise HTTPException(status_code=400, detail="Empty file")

    # Check rate limiting if user_id is provided
    if user_id:
        active_jobs = redis_client.get(f"user_active_jobs:{user_id}")
        if active_jobs and int(active_jobs) >= sett.max_concurrent_jobs_per_user:
            raise HTTPException(
                status_code=429,
                detail=f"Too many active translation jobs. Maximum is {sett.max_concurrent_jobs_per_user}",
            )

        # Increment active jobs counter
        redis_client.incr(f"user_active_jobs:{user_id}")

    # Encode file content
    srt_encoded = base64.b64encode(file_content).decode("utf-8")

    # Queue the processing task
    task = process_srt_task.delay(srt_encoded, file.filename, user_id)

    return {
        "message": "SRT translation queued",
        "task_id": task.id,
        "status_url": f"/job-status/{task.id}",
    }


@app.get("/job-status/{job_id}")
async def get_job_status(job_id: str):
    """Get the status of a translation job"""
    job_info = redis_client.hgetall(f"job:{job_id}")

    if not job_info:
        raise HTTPException(status_code=404, detail="Job not found")

    # Convert byte keys to string
    job_info = {k.decode("utf-8"): v.decode("utf-8") for k, v in job_info.items()}

    # Get language-specific progress
    languages_progress = {}
    language_keys = redis_client.keys(f"translation:{job_id}:*")

    for key in language_keys:
        lang = key.decode("utf-8").split(":")[-1]
        lang_info = redis_client.hgetall(key)

        if lang_info:
            languages_progress[lang] = {
                k.decode("utf-8"): v.decode("utf-8") for k, v in lang_info.items()
            }

    job_info["languages_progress"] = languages_progress

    return job_info


@app.delete("/job/{job_id}")
async def cancel_job(job_id: str, x_api_key: Optional[str] = Header(None)):
    """Cancel a running job"""
    # Validate API key if required
    if sett.require_api_key and x_api_key != sett.api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")

    job_info = redis_client.hgetall(f"job:{job_id}")

    if not job_info:
        raise HTTPException(status_code=404, detail="Job not found")

    # Mark job as cancelled
    redis_client.hset(f"job:{job_id}", "status", "cancelled")

    # Decrement active jobs counter for user if present
    user_id = job_info.get(b"user_id")
    if user_id:
        redis_client.decr(f"user_active_jobs:{user_id.decode('utf-8')}")

    return {"status": "cancelled", "job_id": job_id}


# Scheduled task to clean up old temp directories (can be called by Celery Beat)
@celery.task(name="cleanup_temp_dirs")
def cleanup_temp_dirs(max_age_hours=24):
    """Clean up temporary directories older than max_age_hours"""
    now = time.time()
    count = 0

    for dir_name in os.listdir("."):
        if dir_name.startswith("temp_translations_"):
            dir_path = os.path.join(".", dir_name)
            if os.path.isdir(dir_path):
                # Check directory age
                dir_mtime = os.path.getmtime(dir_path)
                age_hours = (now - dir_mtime) / 3600

                if age_hours > max_age_hours:
                    try:
                        # Recursively remove directory and its contents
                        import shutil

                        shutil.rmtree(dir_path)
                        count += 1
                        log.info(f"Removed old temp directory: {dir_path}")
                    except Exception as e:
                        log.error(f"Error removing directory {dir_path}: {str(e)}")

    return {"removed_directories": count}
