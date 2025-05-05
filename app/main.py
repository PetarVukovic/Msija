from fastapi import FastAPI, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from app.parsing import parse_srt_to_blocks
import logging


log = logging.getLogger()
log.setLevel(logging.INFO)


app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/upload-srt")
async def upload_srt(file: UploadFile = File(...)):
    log.info(f"Recieved file with filename:{file.filename}")
    srt_file_bytes: bytes = await file.read()
    srt_file_list: list[str] = parse_srt_to_blocks(srt_file_bytes)
