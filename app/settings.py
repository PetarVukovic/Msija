from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    llm_model: str = "gemini-2.5-flash-preview-04-17"
    blocks_per_chunk: int = 50
    api_call_delay: float = 2.0
    n8n_webhook_url: str | None = None
    OPENAI_API_KEY: str | None = None
    GOOGLE_API_KEY: str | None = None

    class Config:
        env_file = ".env"


settings = Settings()
