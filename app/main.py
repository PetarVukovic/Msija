# --- app/main.py ---
from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import base64
import logging
from celery import Celery
import os
import asyncio
import httpx
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import SystemMessage, HumanMessage
from typing import List, Dict, Any, Optional

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="SRT Translation Service", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Celery configuration
celery = Celery(
    "SRT_translator",
    broker=os.getenv("REDIS_URL_LOCAL", "redis://localhost:6379/0"),
    backend=os.getenv("REDIS_BACKEND_LOCAL", "redis://localhost:6379/1"),
)

# Language and constants
TARGET_LANGUAGES = ["German", "French", "Spanish"]  # Skratio za primjer
BLOCKS_PER_CHUNK = 50
API_CALL_DELAY_SECONDS = 2.0
INTER_LANGUAGE_DELAY_SECONDS = 2.0
N8N_WEBHOOK_URL = os.getenv("N8N_WEBHOOK_URL")
N8N_TIMEOUT_SECONDS = 60
PREFERRED_LLM_MODEL = "gemini-2.5-flash-preview-04-17"
FALLBACK_LLM_MODEL = "gemini-2.5-flash-preview-04-17"


# Core logic
class LLM:
    def __init__(self, model_name: str):
        self.model = ChatGoogleGenerativeAI(model=model_name)

    async def translate_srt_chunk(self, chunk: str, lang: str) -> str:
        sys = SystemMessage(
            content=f"Translate this SRT chunk to {lang}. Keep formatting exactly the same."
        )
        user = HumanMessage(content=chunk)
        response = await self.model.ainvoke([sys, user])
        return response.content


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


async def parse_srt_to_blocks(srt_bytes: bytes) -> List[str]:
    try:
        text = srt_bytes.decode("utf-8")
    except UnicodeDecodeError:
        text = srt_bytes.decode("latin1")
    return [
        b.strip() for b in text.replace("\r\n", "\n").strip().split("\n\n") if b.strip()
    ]


async def translate_chunk(blocks: List[str], lang: str, llm: LLM) -> str:
    result = []
    for i in range(0, len(blocks), BLOCKS_PER_CHUNK):
        chunk = "\n\n".join(blocks[i : i + BLOCKS_PER_CHUNK])
        translated = await llm.translate_srt_chunk(chunk, lang)
        result.append(translated.strip())
        await asyncio.sleep(API_CALL_DELAY_SECONDS)
    return "\n\n".join(result)


async def send_to_n8n(
    client: httpx.AsyncClient, filename: str, lang: str, content: str
) -> int:
    if not N8N_WEBHOOK_URL:
        return 503
    try:
        resp = await client.post(
            N8N_WEBHOOK_URL,
            json={"filename": filename, "language": lang, "translated_srt": content},
            timeout=N8N_TIMEOUT_SECONDS,
        )
        return resp.status_code
    except Exception:
        return 500


async def process_srt_workflow(srt_bytes: bytes, filename: str) -> List[Dict[str, Any]]:
    blocks = await parse_srt_to_blocks(srt_bytes)
    llm = LLM(PREFERRED_LLM_MODEL)
    temp_dir = f"temp_translations_{filename.replace('.', '_')}"
    os.makedirs(temp_dir, exist_ok=True)
    results = []

    english_srt = await translate_chunk(blocks, "English", llm)
    save_to_disk(temp_dir, "English", filename, english_srt)

    async with httpx.AsyncClient() as client:
        status = await send_to_n8n(client, filename, "English", english_srt)
        results.append({"lang": "English", "status_n8n": status})

        eng_blocks = await parse_srt_to_blocks(english_srt.encode("utf-8"))
        for lang in TARGET_LANGUAGES:
            try:
                translated = await translate_chunk(eng_blocks, lang, llm)
                save_to_disk(temp_dir, lang, filename, translated)
                status = await send_to_n8n(client, filename, lang, translated)
                results.append({"lang": lang, "status_n8n": status})
                await asyncio.sleep(INTER_LANGUAGE_DELAY_SECONDS)
            except Exception as e:
                results.append({"lang": lang, "status_n8n": f"Failed: {e}"})

    return results


@celery.task(name="process_srt_task")
def process_srt_task(srt_b64: str, filename: str):
    srt_bytes = base64.b64decode(srt_b64)
    return asyncio.run(process_srt_workflow(srt_bytes, filename))


@app.post("/uploadSrt")
async def upload_srt_endpoint(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".srt"):
        raise HTTPException(status_code=400, detail="Invalid file type")

    srt_bytes = await file.read()
    if not srt_bytes:
        raise HTTPException(status_code=400, detail="Empty file")

    srt_encoded = base64.b64encode(srt_bytes).decode("utf-8")
    task = process_srt_task.delay(srt_encoded, file.filename)

    return {"message": "SRT translation queued", "task_id": task.id}
