"""Ajustes persistidos en data/settings.json (junto a la app)."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

log = logging.getLogger(__name__)

SPEEDS = [(0.7, "Lento"), (0.85, "0.85×"), (1.0, "1×"), (1.15, "1.15×")]


@dataclass
class Settings:
    accent: str = "en-US"            # en-US | en-GB
    voice_us: str = "af_heart"
    voice_gb: str = "bf_emma"
    speed: float = 1.0
    hotkey: str = "Alt+Shift+P"
    mouse_mode: bool = True          # mostrar el iconito al seleccionar con el mouse
    auto_play: bool = True
    show_ipa: bool = True
    theme: str = "system"            # system | dark | light
    max_chars: int = 2000
    only_english: bool = True
    start_with_windows: bool = False
    card_width: int = 364           # ancho de la burbuja (se cambia arrastrando la esquina)
    text_height: int = 0             # alto del cuadro de texto; 0 = automático
    ignored_apps: list[str] = field(default_factory=lambda: ["KeePass.exe", "1Password.exe", "Bitwarden.exe"])

    _path: Path | None = field(default=None, repr=False, compare=False)

    @classmethod
    def load(cls, path: Path) -> "Settings":
        s = cls()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            names = {f.name for f in fields(cls) if not f.name.startswith("_")}
            for k, v in raw.items():
                if k in names and isinstance(v, type(getattr(s, k))) or (k == "speed" and isinstance(v, (int, float))):
                    setattr(s, k, v)
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as e:
            log.warning("settings.json inválido, uso valores por defecto: %s", e)
        s._path = path
        s.speed = float(s.speed)
        if s.max_chars == 600:  # el límite por defecto anterior pasa al nuevo
            s.max_chars = 2000
        if s.accent not in ("en-US", "en-GB"):
            s.accent = "en-US"
        s.save()
        return s

    def save(self) -> None:
        if not self._path:
            return
        data = {k: v for k, v in asdict(self).items() if not k.startswith("_")}
        try:
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self._path)
        except OSError as e:
            log.warning("No pude guardar los ajustes: %s", e)

    @property
    def voice(self) -> str:
        return self.voice_gb if self.accent == "en-GB" else self.voice_us
