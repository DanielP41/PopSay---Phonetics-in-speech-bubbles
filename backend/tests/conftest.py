import pytest
from app.config import get_settings
from fastapi.testclient import TestClient


@pytest.fixture
def make_client(monkeypatch):
    """Crea un TestClient con variables de entorno propias (sin leer .env)."""

    def _make(**env):
        base = {"TTS_PROVIDER": "fake", "REDIS_URL": "", "API_TOKEN": "", "RATE_LIMIT_PER_MINUTE": "1000"}
        base.update({k.upper(): str(v) for k, v in env.items()})
        for k, v in base.items():
            monkeypatch.setenv(k, v)
        monkeypatch.chdir("/")  # que no encuentre un .env local
        get_settings.cache_clear()
        from app.main import app

        return TestClient(app)

    yield _make
    get_settings.cache_clear()
