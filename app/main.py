from multiprocessing.pool import AsyncResult
from fastapi import FastAPI, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from app.parsing import parse_srt_to_blocks
from app.tasks import sample_task
from celery.result import AsyncResult
from app.celery import app as celery_app
import logging


log = logging.getLogger(name=__name__)
log.setLevel(logging.INFO)


app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/task-status/{task_id}")
def task_status(task_id: str):
    result = AsyncResult(task_id, app=celery_app)
    return {
        "task_id": task_id,
        "state": result.state,
        "result": result.result if result.ready() else None,
    }


@app.post("/upload-srt")
async def upload_srt(file: UploadFile = File(...)):
    log.info(f"Recieved file with filename:{file.filename}")
    srt_file_bytes: bytes = await file.read()
    srt_file_list: list[str] = await parse_srt_to_blocks(srt_file_bytes)
    result = sample_task.delay(5, 5)
    log.info(f"Task ID: {result.id}")
    res = result.get(timeout=10)
    log.info(f"Task result: {res}")
    return {"message": "succes", "data": srt_file_list}
