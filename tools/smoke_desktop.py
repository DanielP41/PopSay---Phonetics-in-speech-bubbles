"""Smoke test de la app de escritorio (sin Windows ni audio): abre la burbuja con un texto,
sintetiza con Kokoro de verdad y guarda capturas en modo oscuro y claro.

Uso:  QT_QPA_PLATFORM=offscreen python tools/smoke_desktop.py
"""

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "desktop"))

from PySide6.QtCore import QPoint, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from popsay import app as appmod  # noqa: E402
from popsay import engine  # noqa: E402


class FakePlayer:
    """Sin placa de sonido en el contenedor: avanza la posición con el reloj."""

    def __init__(self):
        self.t0 = None
        self.dur = 1.0
        self.cb = None

    def play(self, audio, on_finish=None, more=False):
        self.t0, self.n, self.cb, self.closed = time.monotonic(), len(audio), on_finish, not more
        self._arm()

    def append(self, audio):
        self.n += len(audio)
        self._arm()

    def close(self):
        self.closed = True
        self._arm()

    def _arm(self):
        if self.closed:
            left = self.n / engine.SAMPLE_RATE - (time.monotonic() - self.t0)
            cb = self.cb
            QTimer.singleShot(max(0, int(left * 1000)), lambda: cb and self.cb is cb and cb())

    def stop(self):
        self.cb = None

    @property
    def position(self):
        return 0 if self.t0 is None else min(self.n, int((time.monotonic() - self.t0) * engine.SAMPLE_RATE))

    @property
    def progress(self):
        return 0.0 if self.t0 is None else min(1.0, self.position / max(1, self.n))


def main():
    out = ROOT / "tools"
    QApplication.setQuitOnLastWindowClosed(False)
    app = QApplication(sys.argv)
    data = appmod.paths.data_dir()
    appmod.setup_logging(data)
    ctl = appmod.Controller(app)
    ctl.player = FakePlayer()
    ctl.settings.theme = "dark"
    ctl.settings.accent = "en-US"  # ajustes limpios en cada corrida
    ctl.settings.speed = 1.0
    ctl.bubble.apply_theme()
    ctl.engine._ready.wait(60)
    ctl.anchor = QPoint(500, 200)
    results = {}

    def step1():
        ctl._on_text("Required reviews validate approach", "selection", ctl.request)

    def shot(name):
        def f():
            ctl.bubble.grab().save(str(out / f"desktop_{name}.png"))
            results[name] = (ctl.bubble.state, ctl.bubble.active_word, ctl.bubble.ipa_label.text())
        return f

    plays = {"n": 0}

    def on_speech(_sid, i, _speech):
        if i:  # sólo el primer fragmento de cada reproducción
            return
        plays["n"] += 1
        QTimer.singleShot(900, shot("dark_playing" if plays["n"] == 1 else "light_playing"))
        if plays["n"] == 1:
            QTimer.singleShot(3500, light)
        else:
            QTimer.singleShot(3500, shot("light_done"))
            QTimer.singleShot(3700, app.quit)

    def light():
        ctl.settings.theme = "light"
        ctl.bubble.apply_theme()
        ctl._set("accent", "en-GB")
        ctl.play()
        QTimer.singleShot(300, shot("light_loading"))

    ctl.bridge.chunk_ready.connect(on_speech)
    QTimer.singleShot(100, step1)
    QTimer.singleShot(60000, app.quit)
    app.exec()
    for k, v in results.items():
        print(k, v)
    ok = (results.get("dark_playing", ("",))[0] == "playing"
          and results.get("light_loading", ("",))[0] in ("loading", "playing")  # con caché en disco puede sonar al instante
          and "ˈ" in results.get("light_done", ("", "", ""))[2])
    print("OK" if ok else "FALLÓ")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
