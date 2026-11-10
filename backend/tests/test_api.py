import asyncio

import httpx
from app.cache import MemoryCache, audio_key
from app.config import Settings
from app.providers import Audio, AzureProvider, OpenAIProvider


def test_health(make_client):
    with make_client() as c:
        r = c.get("/health")
        assert r.status_code == 200
        assert r.json()["provider"] == "fake"
        assert r.json()["cache"] == "memory"


def test_tts_returns_audio_and_caches(make_client):
    with make_client() as c:
        body = {"text": "Required   reviews validate approach", "accent": "en-US"}
        r1 = c.post("/tts", json=body)
        assert r1.status_code == 200
        assert r1.headers["content-type"] == "audio/wav"
        assert r1.headers["x-cache"] == "MISS"
        assert r1.content[:4] == b"RIFF"
        r2 = c.post("/tts", json={"text": "Required reviews validate approach", "accent": "en-US"})
        assert r2.headers["x-cache"] == "HIT"  # espacios normalizados -> misma clave
        r3 = c.post("/tts", json={**body, "accent": "en-GB"})
        assert r3.headers["x-cache"] == "MISS"


def test_tts_validation(make_client):
    with make_client(max_text_chars=10) as c:
        assert c.post("/tts", json={"text": "   "}).status_code == 422
        assert c.post("/tts", json={"text": "x" * 11}).status_code == 422
        assert c.post("/tts", json={"text": "hello", "accent": "es-AR"}).status_code == 422


def test_tts_without_provider(make_client):
    with make_client(tts_provider="none") as c:
        assert c.post("/tts", json={"text": "hello"}).status_code == 503
        assert c.get("/health").json()["provider"] == "none"


def test_api_token(make_client):
    with make_client(api_token="s3cret") as c:
        assert c.post("/tts", json={"text": "hello"}).status_code == 401
        assert c.get("/ipa", params={"text": "hello"}).status_code == 401
        assert c.post("/tts", json={"text": "hello"}, headers={"X-Api-Key": "nope"}).status_code == 401
        assert c.post("/tts", json={"text": "hello"}, headers={"X-Api-Key": "s3cret"}).status_code == 200
        assert c.get("/health").status_code == 200


def test_rate_limit(make_client):
    with make_client(rate_limit_per_minute=3) as c:
        codes = [c.get("/ipa", params={"text": "hi"}).status_code for _ in range(5)]
        assert codes == [200, 200, 200, 429, 429]


def test_ipa_endpoint(make_client):
    with make_client() as c:
        r = c.get("/ipa", params={"text": "Required reviews validate approach", "accent": "en-GB"})
        assert r.status_code == 200
        data = r.json()
        assert data["ipa"] == "rɪˈkwaɪəd rɪˈvjuːz ˈvælədeɪt əˈprəʊtʃ"
        assert data["approximate"] is True
        assert len(data["words"]) == 4


def test_cors_allows_extension_origin(make_client):
    with make_client() as c:
        r = c.options(
            "/tts",
            headers={
                "Origin": "chrome-extension://abcdefghijklmnop",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type,x-api-key",
            },
        )
        assert r.headers.get("access-control-allow-origin") == "chrome-extension://abcdefghijklmnop"
        r = c.options("/tts", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
        assert "access-control-allow-origin" not in r.headers


def test_memory_cache_evicts_by_size():
    async def run():
        cache = MemoryCache(max_bytes=10, ttl_seconds=60)
        await cache.set("a", Audio(b"12345", "audio/mpeg"))
        await cache.set("b", Audio(b"12345", "audio/mpeg"))
        await cache.set("c", Audio(b"12345", "audio/mpeg"))
        assert await cache.get("a") is None
        assert (await cache.get("c")).data == b"12345"
        assert cache.size == 10

    asyncio.run(run())


def test_audio_key_depends_on_all_parts():
    k = audio_key("azure", "v", "en-US", "hi")
    assert k != audio_key("azure", "v", "en-GB", "hi")
    assert k != audio_key("openai", "v", "en-US", "hi")
    assert k.startswith("popsay:tts:")


def test_azure_request_shape():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["headers"] = request.headers
        seen["body"] = request.content.decode()
        return httpx.Response(200, content=b"ID3fake")

    async def run():
        s = Settings(tts_provider="azure", azure_speech_key="k", azure_speech_region="eastus")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            audio = await AzureProvider(s, client).synthesize("Tom & <Jerry>", "en-GB")
        assert audio.media_type == "audio/mpeg"

    asyncio.run(run())
    assert seen["url"] == "https://eastus.tts.speech.microsoft.com/cognitiveservices/v1"
    assert seen["headers"]["ocp-apim-subscription-key"] == "k"
    assert 'name="en-GB-SoniaNeural"' in seen["body"]
    assert "Tom &amp; &lt;Jerry&gt;" in seen["body"]


def test_openai_request_includes_accent_instructions():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen["json"] = json.loads(request.content)
        return httpx.Response(200, content=b"ID3fake")

    async def run():
        s = Settings(tts_provider="openai", openai_api_key="k")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await OpenAIProvider(s, client).synthesize("hello", "en-GB")

    asyncio.run(run())
    assert seen["json"]["input"] == "hello"
    assert "British" in seen["json"]["instructions"]


def test_provider_error_maps_to_502(make_client, monkeypatch):
    from app.providers import FakeProvider, ProviderError

    async def boom(self, text, accent):
        raise ProviderError("cuota agotada")

    monkeypatch.setattr(FakeProvider, "synthesize", boom)
    with make_client() as c:
        assert c.post("/tts", json={"text": "hello"}).status_code == 502
