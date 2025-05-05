import logging

log = logging.getLogger()
log.setLevel(logging.INFO)


async def parse_srt_to_blocks(srt_file_bytes: bytes) -> list[str]:
    """
    Parsira sadržaj SRT datoteke (kao bytes) u listu stringova,
    gdje svaki string predstavlja jedan SRT blok (broj, vrijeme, tekst).

    Args:
        srt_file_bytes: Sadržaj SRT datoteke kao bytes.

    Returns:
        Lista stringova gdje svaki element predstavlja jedan SRT blok.
        Vraća praznu listu ako je datoteka prazna ili dođe do greške pri parsiranju.
    """
    if not srt_file_bytes:
        log.warning("Input SRT file bytes are empty.")
        return []

    try:
        srt_text = srt_file_bytes.decode("utf-8")
        normalized_text = srt_text.replace("\r\n", "\n").strip()
        srt_blocks = [
            block.strip() for block in normalized_text.split("\n\n") if block.strip()
        ]

        if not srt_blocks:
            log.warning("SRT file parsed into zero valid blocks.")
        else:
            log.info(f"Successfully parsed SRT file into {len(srt_blocks)} blocks.")
        return srt_blocks
    except Exception as e:
        log.error(f"Error occurred while parsing SRT file to list: {e}")
        return []
