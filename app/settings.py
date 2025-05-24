from pydantic_settings import BaseSettings
from typing import List
import os
from dotenv import load_dotenv

# Učitaj .env datoteku ako postoji
load_dotenv()


class Settings(BaseSettings):
    # LLM postavke
    llm_model: str = os.getenv("LLM_MODEL", "gemini-pro")
    api_call_delay: float = float(
        os.getenv("API_CALL_DELAY", "1.0")
    )  # Sekunde između API poziva
    blocks_per_chunk: int = int(
        os.getenv("BLOCKS_PER_CHUNK", "5")
    )  # Broj SRT blokova po API pozivu

    # N8N webhook postavke
    n8n_webhook_url: str = os.getenv("N8N_WEBHOOK_URL", "")
    n8n_timeout: int = int(os.getenv("N8N_TIMEOUT", "30"))  # Timeout za webhook pozive

    # Ograničenja korisnika
    max_concurrent_jobs_per_user: int = int(
        os.getenv("MAX_CONCURRENT_JOBS_PER_USER", "3")
    )
    max_file_size_mb: int = int(
        os.getenv("MAX_FILE_SIZE_MB", "10")
    )  # Maksimalna veličina datoteke u MB

    # API sigurnost
    require_api_key: bool = os.getenv("REQUIRE_API_KEY", "false").lower() == "true"
    api_key: str = os.getenv("API_KEY", "")

    # Lista ciljnih jezika
    target_languages: List[str] = os.getenv(
        "TARGET_LANGUAGES",
        "English,Croatian,Serbian,German,French,Spanish,Italian,Chinese,Japanese,Korean",
    ).split(",")

    # Redis postavke
    redis_url: str = os.getenv("REDIS_URL_LOCAL", "redis://localhost:6379/0")
    redis_backend: str = os.getenv("REDIS_BACKEND_LOCAL", "redis://localhost:6379/1")

    # Putanje za spremanje datoteka
    output_directory: str = os.getenv("OUTPUT_DIRECTORY", "translations")

    # Postavke čišćenja i održavanja
    cleanup_interval_hours: int = int(
        os.getenv("CLEANUP_INTERVAL_HOURS", "24")
    )  # Interval za čišćenje starih datoteka
    temp_file_max_age_hours: int = int(
        os.getenv("TEMP_FILE_MAX_AGE_HOURS", "72")
    )  # Maksimalna starost privremenih datoteka

    # Postavke za praćenje napretka
    progress_update_interval: float = float(
        os.getenv("PROGRESS_UPDATE_INTERVAL", "5.0")
    )  # Interval za ažuriranje napretka

    # Postavke za ponovno pokušavanje i oporavak
    max_translation_retries: int = int(os.getenv("MAX_TRANSLATION_RETRIES", "3"))
    initial_retry_delay: int = int(
        os.getenv("INITIAL_RETRY_DELAY", "60")
    )  # Početni delay za retries u sekundama
    stalled_job_timeout: int = int(
        os.getenv("STALLED_JOB_TIMEOUT", "1800")
    )  # Timeout za zastale poslove u sekundama

    class Config:
        env_file = ".env"
        case_sensitive = False


settings = Settings()
