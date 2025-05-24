import os
import sys
import argparse
import logging
import redis
from dotenv import load_dotenv, find_dotenv, set_key


logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
log = logging.getLogger("init")


def create_env_file():
    """Stvara .env datoteku s osnovnim postavkama ako ne postoji"""
    env_path = find_dotenv()
    if not env_path:
        log.info("Stvaram novu .env datoteku...")
        with open(".env", "w") as f:
            f.write(
                """# SRT Translation Service postavke
# LLM postavke
LLM_MODEL=gemini-pro
API_CALL_DELAY=1.0
BLOCKS_PER_CHUNK=5

# N8N webhook
N8N_WEBHOOK_URL=
N8N_TIMEOUT=30

# Ograničenja
MAX_CONCURRENT_JOBS_PER_USER=3
MAX_FILE_SIZE_MB=10

# API sigurnost
REQUIRE_API_KEY=false
API_KEY=

# Lista ciljnih jezika (odvojeno zarezima)
TARGET_LANGUAGES=English,Croatian,Serbian,German,French,Spanish,Italian,Chinese,Japanese,Korean

# Redis postavke
REDIS_URL_LOCAL=redis://localhost:6379/0
REDIS_BACKEND_LOCAL=redis://localhost:6379/1

# Putanje za spremanje datoteka
OUTPUT_DIRECTORY=translations

# Postavke čišćenja
CLEANUP_INTERVAL_HOURS=24
TEMP_FILE_MAX_AGE_HOURS=72

# Napredak i oporavak
PROGRESS_UPDATE_INTERVAL=5.0
MAX_TRANSLATION_RETRIES=3
INITIAL_RETRY_DELAY=60
STALLED_JOB_TIMEOUT=1800
"""
            )
        log.info("Datoteka .env uspješno stvorena.")
        return ".env"
    return env_path


def check_redis_connection():
    """Provjerava vezu s Redis serverom"""
    load_dotenv()
    redis_url = os.getenv("REDIS_URL_LOCAL", "redis://localhost:6379/0")
    try:
        r = redis.Redis.from_url(redis_url)
        r.ping()
        log.info(f"✅ Uspješno spojen na Redis server: {redis_url}")
        return True
    except redis.ConnectionError as e:
        log.error(f"❌ Greška pri spajanju na Redis server {redis_url}: {e}")
        log.error("Provjerite je li Redis server pokrenut.")
        return False


def create_directories():
    """Stvara potrebne direktorije za rad aplikacije"""
    load_dotenv()
    output_dir = os.getenv("OUTPUT_DIRECTORY", "translations")

    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
        log.info(f"✅ Stvoren direktorij za prijevode: {output_dir}")
    else:
        log.info(f"✅ Direktorij za prijevode već postoji: {output_dir}")

    return True


def configure_languages(languages=None):
    """Postavlja ciljne jezike za prijevod"""
    env_path = find_dotenv()
    current_langs = os.getenv("TARGET_LANGUAGES", "").split(",")

    if languages:
        # Postavi nove jezike
        set_key(env_path, "TARGET_LANGUAGES", ",".join(languages))
        log.info(f"✅ Postavljeni ciljni jezici: {languages}")
    else:
        log.info(f"ℹ️ Trenutni ciljni jezici: {current_langs}")

    return True


def main():
    parser = argparse.ArgumentParser(
        description="Inicijalizacija SRT Translation Service"
    )
    parser.add_argument(
        "--create-env", action="store_true", help="Stvori .env datoteku"
    )
    parser.add_argument(
        "--check-redis", action="store_true", help="Provjeri Redis vezu"
    )
    parser.add_argument(
        "--create-dirs", action="store_true", help="Stvori potrebne direktorije"
    )
    parser.add_argument("--languages", help="Postavi ciljne jezike (odvojeno zarezima)")
    parser.add_argument("--all", action="store_true", help="Izvrši sve provjere")

    args = parser.parse_args()

    if args.all or len(sys.argv) == 1:
        create_env_file()
        check_redis_connection()
        create_directories()
    else:
        if args.create_env:
            create_env_file()
        if args.check_redis:
            check_redis_connection()
        if args.create_dirs:
            create_directories()
        if args.languages:
            languages = [lang.strip() for lang in args.languages.split(",")]
            configure_languages(languages)

    log.info("Inicijalizacija završena. Sustav je spreman za korištenje.")


if __name__ == "__main__":
    main()
