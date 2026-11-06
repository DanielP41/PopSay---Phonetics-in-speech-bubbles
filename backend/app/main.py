"""PopSay — backend de pronunciación (TTS neuronal + IPA)."""

from __future__ import annotations

import logging
import secrets
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from typing import Annotated, Literal

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator

from . import __version__
from .cache import audio_key, build_cache
from .config import Settings, get_settings
from .ipa import transcribe
from .providers import ProviderError, build_provider

log = logging.getLogger("popsay")

Accent = Literal["en-US", "en-GB"]


class RateLimiter:
    """Ventana deslizante de 60 s por cliente (en memoria, por proceso)."""

    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self.hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, client: str) -> bool:
        if self.per_minute <= 0:
            return True
        now = time.monotonic()
        q = self.hits[client]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= self.per_minute:
            return False
        q.append(now)
        return True


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    client = httpx.AsyncClient(timeout=s.request_timeout_seconds)
    app.state.http = client
    app.state.provider = build_provider(s, client)
    app.state.cache = build_cache(s.redis_url, s.cache_ttl_seconds, s.memory_cache_max_mb)
    app.state.limiter = RateLimiter(s.rate_limit_per_minute)
    log.info("provider=%s cache=%s", s.tts_provider, app.state.cache.name)
    try:
        yield
    finally:
        await app.state.cache.close()
        await client.aclose()


app = FastAPI(title="PopSay API", version=__version__, lifespan=lifespan)

_settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=_settings.allowed_origin_regex,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-Api-Key"],
    expose_headers=["X-Cache"],
)


def require_token(
    x_api_key: Annotated[str | None, Header()] = None,
    s: Settings = Depends(get_settings),
) -> None:
    if s.api_token and not (x_api_key and secrets.compare_digest(x_api_key, s.api_token)):
        raise HTTPException(status_code=401, detail="API key inválida o ausente")


def rate_limit(request: Request) -> None:
    client = request.headers.get("X-Api-Key") or (request.client.host if request.client else "anon")
    if not request.app.state.limiter.check(client):
        raise HTTPException(status_code=429, detail="Demasiadas solicitudes; probá en un minuto")


class TTSRequest(BaseModel):
    text: str = Field(min_length=1)
    accent: Accent = "en-US"

    @field_validator("text")
    @classmethod
    def clean(cls, v: str) -> str:
        v = " ".join(v.split())
        if not v:
            raise ValueError("texto vacío")
        limit = get_settings().max_text_chars
        if len(v) > limit:
            raise ValueError(f"máximo {limit} caracteres")
        return v


class WordOut(BaseModel):
    word: str
    ipa: str | None
    source: str


class IpaOut(BaseModel):
    text: str
    accent: Accent
    ipa: str
    approximate: bool
    unknown: list[str]
    words: list[WordOut]


@app.get("/health")
async def health(request: Request) -> dict:
    provider = request.app.state.provider
    return {
        "status": "ok",
        "version": __version__,
        "provider": provider.name if provider else "none",
        "cache": request.app.state.cache.name,
    }


@app.post(
    "/tts",
    dependencies=[Depends(require_token), Depends(rate_limit)],
    responses={200: {"content": {"audio/mpeg": {}, "audio/wav": {}}}},
)
async def tts(body: TTSRequest, request: Request) -> Response:
    provider = request.app.state.provider
    if provider is None:
        raise HTTPException(status_code=503, detail="Ningún proveedor de TTS configurado (TTS_PROVIDER)")
    cache = request.app.state.cache
    key = audio_key(provider.name, provider.voice_id(body.accent), body.accent, body.text)

    audio = None
    try:
        audio = await cache.get(key)
    except Exception as e:  # caché caída: seguimos sin ella
        log.warning("cache get falló: %s", e)
    hit = audio is not None

    if audio is None:
        try:
            audio = await provider.synthesize(body.text, body.accent)
        except ProviderError as e:
            log.error("%s", e)
            raise HTTPException(status_code=502, detail="El proveedor de voz falló") from e
        except httpx.HTTPError as e:
            raise HTTPException(status_code=504, detail="El proveedor de voz no respondió") from e
        try:
            await cache.set(key, audio)
        except Exception as e:
            log.warning("cache set falló: %s", e)

    return Response(
        content=audio.data,
        media_type=audio.media_type,
        headers={"X-Cache": "HIT" if hit else "MISS", "Cache-Control": "private, max-age=86400"},
    )


@app.get("/ipa", response_model=IpaOut, dependencies=[Depends(require_token), Depends(rate_limit)])
async def ipa(
    text: Annotated[str, Query(min_length=1, max_length=2000)],
    accent: Accent = "en-US",
) -> IpaOut:
    clean = " ".join(text.split())
    if len(clean) > get_settings().max_text_chars:
        raise HTTPException(status_code=422, detail="Texto demasiado largo")
    r = transcribe(clean, accent)
    return IpaOut(
        text=clean,
        accent=accent,
        ipa=r.ipa,
        approximate=r.approximate,
        unknown=r.unknown,
        words=[WordOut(word=w.word, ipa=w.ipa, source=w.source) for w in r.words],
    )
