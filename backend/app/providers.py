"""Proveedores de TTS neuronal. Todos devuelven audio comprimido listo para <audio>.

La velocidad NO se pide al proveedor: el cliente la aplica con playbackRate, así
una misma frase queda cacheada una sola vez para todas las velocidades.
"""

from __future__ import annotations

import io
import math
import struct
import wave
from dataclasses import dataclass
from html import escape
from typing import Protocol

import httpx

from .config import Settings


class ProviderError(RuntimeError):
    """Error del proveedor externo (credenciales, cuota, red)."""


@dataclass
class Audio:
    data: bytes
    media_type: str


class TTSProvider(Protocol):
    name: str

    def voice_id(self, accent: str) -> str: ...

    async def synthesize(self, text: str, accent: str) -> Audio: ...


def _raise_for(resp: httpx.Response, provider: str) -> None:
    if resp.status_code >= 400:
        body = resp.text[:300]
        raise ProviderError(f"{provider} respondió {resp.status_code}: {body}")


class AzureProvider:
    name = "azure"

    def __init__(self, s: Settings, client: httpx.AsyncClient):
        if not (s.azure_speech_key and s.azure_speech_region):
            raise ValueError("AZURE_SPEECH_KEY y AZURE_SPEECH_REGION son obligatorios")
        self.s, self.client = s, client

    def voice_id(self, accent: str) -> str:
        return self.s.azure_voice_gb if accent == "en-GB" else self.s.azure_voice_us

    async def synthesize(self, text: str, accent: str) -> Audio:
        voice = self.voice_id(accent)
        ssml = (
            f'<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="{accent}">'
            f'<voice name="{escape(voice)}">{escape(text)}</voice></speak>'
        )
        resp = await self.client.post(
            f"https://{self.s.azure_speech_region}.tts.speech.microsoft.com/cognitiveservices/v1",
            content=ssml.encode("utf-8"),
            headers={
                "Ocp-Apim-Subscription-Key": self.s.azure_speech_key,
                "Content-Type": "application/ssml+xml",
                "X-Microsoft-OutputFormat": "audio-24khz-48kbitrate-mono-mp3",
                "User-Agent": "popsay",
            },
        )
        _raise_for(resp, "Azure")
        return Audio(resp.content, "audio/mpeg")


class OpenAIProvider:
    name = "openai"

    def __init__(self, s: Settings, client: httpx.AsyncClient):
        if not s.openai_api_key:
            raise ValueError("OPENAI_API_KEY es obligatorio")
        self.s, self.client = s, client

    def voice_id(self, accent: str) -> str:
        return f"{self.s.openai_tts_model}:{self.s.openai_voice}:{accent}"

    async def synthesize(self, text: str, accent: str) -> Audio:
        payload: dict = {
            "model": self.s.openai_tts_model,
            "voice": self.s.openai_voice,
            "input": text,
            "response_format": "mp3",
        }
        if not self.s.openai_tts_model.startswith("tts-1"):
            region = "British (Received Pronunciation)" if accent == "en-GB" else "General American"
            payload["instructions"] = (
                f"Speak with a clear, natural {region} English accent, as a pronunciation teacher would: "
                "neutral tone, careful articulation, no added words."
            )
        resp = await self.client.post(
            "https://api.openai.com/v1/audio/speech",
            json=payload,
            headers={"Authorization": f"Bearer {self.s.openai_api_key}"},
        )
        _raise_for(resp, "OpenAI")
        return Audio(resp.content, "audio/mpeg")


class ElevenLabsProvider:
    name = "elevenlabs"

    def __init__(self, s: Settings, client: httpx.AsyncClient):
        if not (s.elevenlabs_api_key and s.elevenlabs_voice_us):
            raise ValueError("ELEVENLABS_API_KEY y ELEVENLABS_VOICE_US son obligatorios")
        self.s, self.client = s, client

    def voice_id(self, accent: str) -> str:
        if accent == "en-GB" and self.s.elevenlabs_voice_gb:
            return self.s.elevenlabs_voice_gb
        return self.s.elevenlabs_voice_us

    async def synthesize(self, text: str, accent: str) -> Audio:
        resp = await self.client.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{self.voice_id(accent)}",
            params={"output_format": "mp3_44100_128"},
            json={"text": text, "model_id": self.s.elevenlabs_model},
            headers={"xi-api-key": self.s.elevenlabs_api_key, "Accept": "audio/mpeg"},
        )
        _raise_for(resp, "ElevenLabs")
        return Audio(resp.content, "audio/mpeg")


class FakeProvider:
    """Proveedor de pruebas: un tono corto en WAV, proporcional al largo del texto."""

    name = "fake"

    def __init__(self, *_args, **_kwargs):
        self.calls = 0

    def voice_id(self, accent: str) -> str:
        return f"fake:{accent}"

    async def synthesize(self, text: str, accent: str) -> Audio:
        self.calls += 1
        rate = 8000
        seconds = min(3.0, 0.15 + 0.03 * len(text))
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            freq = 440 if accent == "en-US" else 523
            frames = b"".join(
                struct.pack("<h", int(3000 * math.sin(2 * math.pi * freq * i / rate))) for i in range(int(rate * seconds))
            )
            w.writeframes(frames)
        return Audio(buf.getvalue(), "audio/wav")


def build_provider(s: Settings, client: httpx.AsyncClient) -> TTSProvider | None:
    match s.tts_provider:
        case "azure":
            return AzureProvider(s, client)
        case "openai":
            return OpenAIProvider(s, client)
        case "elevenlabs":
            return ElevenLabsProvider(s, client)
        case "fake":
            return FakeProvider()
        case _:
            return None
