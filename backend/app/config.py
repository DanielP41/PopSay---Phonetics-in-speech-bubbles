from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    tts_provider: Literal["none", "azure", "openai", "elevenlabs", "fake"] = "none"

    azure_speech_key: str = ""
    azure_speech_region: str = ""
    azure_voice_us: str = "en-US-JennyNeural"
    azure_voice_gb: str = "en-GB-SoniaNeural"

    openai_api_key: str = ""
    openai_tts_model: str = "gpt-4o-mini-tts"
    openai_voice: str = "alloy"

    elevenlabs_api_key: str = ""
    elevenlabs_model: str = "eleven_multilingual_v2"
    elevenlabs_voice_us: str = ""
    elevenlabs_voice_gb: str = ""

    redis_url: str = ""                     # vacío = caché en memoria
    cache_ttl_seconds: int = 60 * 60 * 24 * 30
    memory_cache_max_mb: int = 64

    api_token: str = ""                     # vacío = sin autenticación (sólo para uso local)
    allowed_origin_regex: str = r"^(chrome|moz)-extension://[a-z0-9-]+$"
    max_text_chars: int = 2000
    rate_limit_per_minute: int = 60
    request_timeout_seconds: float = 20.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
