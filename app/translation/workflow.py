from app.translation.languages import languages
from app.translation.llm import LLM
from app.settings import settings


async def cro2eng(block: str, llm: LLM):
    eng_block = llm.croatian2english(block)
    return eng_block


async def translate_srt(srt: list[str]):
    eng_srt_list = []
    llm = LLM(settings.llm_model)
    for block in srt:
        eng_block = await cro2eng(block, llm)
        eng_srt_list.append(eng_block)

    for language in languages:
        for block in eng_srt_list:
            translated_block = await llm.english2translate(block, language)
