"""Punto de entrada: bandeja del sistema + burbuja + atajo global, todo offline."""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from logging.handlers import RotatingFileHandler

from PySide6.QtCore import QObject, QPoint, QTimer, QUrl, Signal
from PySide6.QtGui import QCursor, QDesktopServices, QGuiApplication, QIcon
from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox, QSystemTrayIcon

from . import APP_NAME, __version__, paths, textutil, winsys
from .engine import SAMPLE_RATE, Engine, Player
from .ipa import IpaDictionary
from .settings import Settings
from .ui import Bubble, TriggerIcon, build_menu

log = logging.getLogger("popsay")


def setup_logging(data_dir) -> None:
    handler = RotatingFileHandler(data_dir / "log.txt", maxBytes=512_000, backupCount=1, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    if sys.stderr:
        root.addHandler(logging.StreamHandler())


class Bridge(QObject):
    """Señales para pasar eventos de otros hilos al hilo de la UI."""

    hotkey = Signal()
    gesture = Signal(int, int)
    mouse_down = Signal(int, int)
    text_ready = Signal(str, str, int)        # texto, origen, id de pedido
    ipa_ready = Signal(int, object, bool)     # id de sesión, IPA por palabra, aproximado
    chunk_ready = Signal(int, int, object)    # id de sesión, n.º de fragmento, Speech
    speech_failed = Signal(int, str)
    playback_done = Signal(int)
    engine_ready = Signal(str)                # "" o mensaje de error


class Controller(QObject):
    def __init__(self, app: QApplication):
        super().__init__()
        self.app = app
        self.data_dir = paths.data_dir()
        self.settings = Settings.load(self.data_dir / "settings.json")
        self.voices = json.loads(paths.find(paths.VOICES_JSON).read_text(encoding="utf-8"))
        self.bridge = Bridge()
        self.engine = Engine(cache_dir=self.data_dir / "cache")
        self.player = Player()
        self.ipa = IpaDictionary(paths.find(paths.IPA_FILE), self.engine.phonemize)
        self.misc = ThreadPoolExecutor(max_workers=2, thread_name_prefix="misc")
        self.session = 0
        self.request = 0
        self.chunks: list[tuple[str, int]] = []
        self.parts: list[dict] = []
        self.futures = []

        self.bubble = Bubble(self.settings)
        self.trigger = TriggerIcon()
        self._wire()
        self._tray()
        self.watcher = winsys.InputWatcher(
            self.settings.hotkey,
            on_hotkey=self.bridge.hotkey.emit,
            on_selection_gesture=lambda x, y: self.bridge.gesture.emit(x, y),
            on_mouse_down=lambda x, y: self.bridge.mouse_down.emit(x, y),
        )
        self.watcher.mouse_enabled = self.settings.mouse_mode
        self.watcher.start()

        self.ticker = QTimer(self, interval=33, timeout=self._tick)
        f = self.engine.start()
        f.add_done_callback(lambda _f: self.bridge.engine_ready.emit(str(self.engine.error or "")))

    def _wire(self):
        b = self.bridge
        b.hotkey.connect(self.pronounce_selection)
        b.gesture.connect(self._on_gesture)
        b.mouse_down.connect(self._on_mouse_down)
        b.text_ready.connect(self._on_text)
        b.ipa_ready.connect(self._on_ipa)
        b.chunk_ready.connect(self._on_chunk)
        b.speech_failed.connect(self._on_speech_failed)
        b.playback_done.connect(self._on_playback_done)
        b.engine_ready.connect(self._on_engine_ready)
        self.trigger.clicked.connect(self.pronounce_selection)
        bub = self.bubble
        bub.play_requested.connect(self.play)
        bub.stop_requested.connect(self.stop)
        bub.closed.connect(self.stop)
        bub.accent_changed.connect(lambda a: self._set("accent", a))
        bub.speed_changed.connect(lambda v: self._set("speed", v))
        bub.settings_requested.connect(self._show_menu_at)

    def _tray(self):
        icon = QIcon(str(paths.asset("icon.png")))
        self.app.setWindowIcon(icon)
        self.tray = QSystemTrayIcon(icon, self.app)
        self.tray.setToolTip(f"{APP_NAME} — cargando la voz…")
        self.tray.activated.connect(self._on_tray_activated)
        self._rebuild_menu()
        self.tray.show()

    def _menu_actions(self):
        return {
            "pronounce": self.pronounce_selection,
            "clipboard": self.pronounce_clipboard,
            "type": self.pronounce_typed,
            "data": lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.data_dir))),
            "about": self._about,
            "quit": self.quit,
        }

    def _rebuild_menu(self):
        self.tray_menu = build_menu(self.settings, self.voices, self._on_setting_changed, actions=self._menu_actions())
        self.tray.setContextMenu(self.tray_menu)

    def _show_menu_at(self, pos: QPoint):
        menu = build_menu(self.settings, self.voices, self._on_setting_changed)
        menu.exec(pos)

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.Trigger:
            self.pronounce_clipboard()

    def _set(self, attr: str, value):
        setattr(self.settings, attr, value)
        self.settings.save()
        self._on_setting_changed(attr)

    def _on_setting_changed(self, attr: str):
        self.bubble.sync_controls()
        if attr == "theme":
            self.bubble.apply_theme()
        if attr == "mouse_mode":
            self.watcher.mouse_enabled = self.settings.mouse_mode
        if attr == "start_with_windows":
            winsys.set_start_with_windows(self.settings.start_with_windows, launch_command())
        if attr in ("accent", "show_ipa") and self.bubble.isVisible():
            self._request_ipa()
        if attr in ("accent", "voice_us", "voice_gb", "speed") and self.bubble.state != "idle":
            self.play()
        self._rebuild_menu()

    def _on_gesture(self, _x: int, _y: int):
        if not self.settings.mouse_mode or self.bubble.isVisible():
            return
        _cls, exe = winsys.foreground_info()
        if exe and exe.lower() in {a.lower() for a in self.settings.ignored_apps}:
            return
        self.trigger.popup(QCursor.pos())

    def _on_mouse_down(self, x: int, y: int):
        if winsys.window_belongs_to_us_at(x, y):
            return
        if self.trigger.isVisible():
            self.trigger.hide()
        if self.bubble.isVisible():
            self.bubble.close_bubble()

    def pronounce_selection(self):
        self.request += 1
        req = self.request
        self.anchor = QCursor.pos()

        def work():
            text, origin = winsys.copy_selection()
            self.bridge.text_ready.emit(text or "", origin, req)

        if winsys.IS_WIN:
            self.misc.submit(work)
        else:  # desarrollo en Linux: usa la selección primaria de X11 o el portapapeles
            cb = QGuiApplication.clipboard()
            self._on_text(cb.text(cb.Selection) or cb.text(), "selection", req)

    def pronounce_clipboard(self):
        self.request += 1
        self.anchor = QCursor.pos()
        self._on_text(QGuiApplication.clipboard().text(), "clipboard", self.request)

    def pronounce_typed(self):
        text, ok = QInputDialog.getMultiLineText(None, APP_NAME, "Texto en inglés para escuchar:")
        if ok and text.strip():
            self.request += 1
            screen = QGuiApplication.primaryScreen().availableGeometry()
            self.anchor = QPoint(screen.center().x(), screen.top() + screen.height() // 3)
            self._on_text(text, "typed", self.request)

    def _on_text(self, raw: str, origin: str, req: int):
        if req != self.request:
            return
        text = textutil.normalize(raw)
        if not text:
            msg = ("En la terminal, copiá el texto primero y usá «Pronunciar portapapeles»."
                   if origin == "clipboard" and winsys.IS_WIN else "No encontré texto seleccionado.")
            self.tray.showMessage(APP_NAME, msg, QSystemTrayIcon.Information, 2500)
            return
        notice = ""
        if self.settings.only_english and origin != "typed" and not textutil.is_likely_english(text):
            notice = "No parece texto en inglés; la pronunciación puede sonar rara."
        text, cut = textutil.truncate(text, self.settings.max_chars)
        if cut:
            notice = f"Texto recortado a {self.settings.max_chars} caracteres."
        if origin == "clipboard":
            notice = notice or "Leído del portapapeles."
        self.stop()
        self.bubble.show_text(text, getattr(self, "anchor", QCursor.pos()), notice)
        if not self.engine.ready and not self.engine.error:
            self.bubble.set_notice("Cargando la voz (sólo la primera vez)…")
        self._request_ipa()
        if self.settings.auto_play:
            self.play()

    def _request_ipa(self):
        if not self.settings.show_ipa:
            return
        sid, text, accent = self.session, self.bubble.text, self.settings.accent

        def work():
            words = [w.text for w in textutil.split_words(text)]
            if not self.engine.ready:  # mientras carga la voz, el diccionario solo; después se afina
                self.bridge.ipa_ready.emit(sid, *self.ipa.transcribe_words(words, accent, in_context=False))
            self.bridge.ipa_ready.emit(sid, *self.ipa.transcribe_words(words, accent))

        self.misc.submit(work)

    def _on_ipa(self, sid: int, ipa, approx: bool):
        if sid == self.session and self.bubble.isVisible():
            self.bubble.set_ipa(ipa, approx)

    def play(self):
        """Sintetiza por fragmentos: el primero (corto) suena apenas está listo y el resto llega mientras suena."""
        if not self.bubble.text:
            return
        self._cancel_pending()
        self.player.stop()
        self.ticker.stop()
        self.session += 1
        sid = self.session
        self.bubble.set_state("loading")
        s = self.settings
        self.chunks = textutil.plan_chunks(self.bubble.text, self.engine.rtf, s.speed)
        self.parts = []
        self.appended = 0
        for i, (txt, _off) in enumerate(self.chunks):
            fut = self.engine.synthesize(txt, s.voice, s.accent, s.speed)

            def done(f, i=i):
                if f.cancelled():
                    return
                try:
                    self.bridge.chunk_ready.emit(sid, i, f.result())
                except Exception as e:  # noqa: BLE001
                    log.exception("Falló la síntesis")
                    self.bridge.speech_failed.emit(sid, str(e))

            fut.add_done_callback(done)
            self.futures.append(fut)
        self._request_ipa()

    def _cancel_pending(self):
        for f in self.futures:
            f.cancel()  # los que todavía no empezaron no ocupan el motor
        self.futures = []

    def _on_chunk(self, sid: int, i: int, speech):
        if sid != self.session or not self.bubble.isVisible():
            return
        txt, off = self.chunks[i]
        words = textutil.split_words(txt)
        weights = speech.weights if speech.weights and len(speech.weights) == len(words) else None
        self.parts.append({
            "start": self.appended, "n": len(speech.audio), "nwords": len(words),
            "w0": len(textutil.split_words(self.bubble.text[:off])),
            "tl": textutil.build_timeline([w.text for w in words], weights),
        })
        self.appended += len(speech.audio)
        last = i == len(self.chunks) - 1
        if i > 0:
            self.player.append(speech.audio)
            if last:
                self.player.close()
            return
        try:
            self.player.play(speech.audio, on_finish=lambda: self.bridge.playback_done.emit(sid), more=not last)
        except Exception as e:  # sin dispositivo de audio, etc.
            log.exception("No pude reproducir")
            self._on_speech_failed(sid, f"No pude reproducir el audio: {e}")
            return
        if self.bubble.notice.text().startswith("Cargando"):
            self.bubble.set_notice("")
        self.bubble.set_state("playing")
        self.ticker.start()

    def _on_speech_failed(self, sid: int, msg: str):
        if sid != self.session:
            return
        self._cancel_pending()
        self.player.stop()
        self.ticker.stop()
        self.bubble.set_state("idle")
        self.bubble.set_notice(msg)

    def _tick(self):
        if not self.parts:
            return
        pos = self.player.position
        part = next((p for p in self.parts if pos < p["start"] + p["n"]), self.parts[-1])
        local = (pos - part["start"]) / max(1, part["n"])
        # Kokoro deja ~0.15 s de silencio al principio y al final de cada fragmento: ajustamos.
        dur = part["n"] / SAMPLE_RATE
        lead = min(0.15 / max(dur, 0.3), 0.2)
        eff = min(1.0, max(0.0, (local - lead) / max(1 - 2 * lead, 0.5)))
        local_word, frac = textutil.position_at(part["tl"], eff)
        word = part["w0"] + local_word
        progress = (part["w0"] + eff * part["nwords"]) / max(1, len(self.bubble.words))
        self.bubble.set_progress(progress, word, frac)

    def _on_playback_done(self, sid: int):
        if sid != self.session:
            return
        self.ticker.stop()
        self.bubble.set_state("idle")
        if self.bubble.repeat and self.bubble.isVisible():
            QTimer.singleShot(450, lambda: self.bubble.repeat and self.bubble.isVisible() and self.play())

    def stop(self):
        self._cancel_pending()
        self.session += 1
        self.ticker.stop()
        self.player.stop()
        if self.bubble.state != "idle":
            self.bubble.set_state("idle")

    def _on_engine_ready(self, error: str):
        if error:
            self.tray.setToolTip(f"{APP_NAME} — error al cargar la voz")
            self.tray.showMessage(APP_NAME, f"No se pudo cargar la voz: {error}", QSystemTrayIcon.Critical, 6000)
            if self.bubble.isVisible():
                self.bubble.set_notice(f"No se pudo cargar la voz: {error}")
            return
        self.tray.setToolTip(f"{APP_NAME} — listo ({self.settings.hotkey})")
        if self.bubble.notice.text().startswith("Cargando"):
            self.bubble.set_notice("")
        self.watcher.hotkey_ok.wait(2)
        if self.watcher.hotkey_error and winsys.IS_WIN:
            self.tray.showMessage(APP_NAME, f"No pude registrar el atajo: {self.watcher.hotkey_error}. "
                                  "Cambialo en data/settings.json.", QSystemTrayIcon.Warning, 6000)
        elif not (self.data_dir / ".welcomed").exists():
            self.tray.showMessage(APP_NAME, f"Listo, sin internet. Seleccioná texto en inglés y presioná "
                                  f"{self.settings.hotkey}, o tocá el iconito azul.", QSystemTrayIcon.Information, 6000)
            try:
                (self.data_dir / ".welcomed").write_text("1", encoding="utf-8")
            except OSError:
                pass

    def _about(self):
        QMessageBox.about(None, APP_NAME, (
            f"<b>{APP_NAME}</b> {__version__}<br><br>"
            "Pronunciación de inglés 100 % offline.<br>"
            "Voz: Kokoro-82M (Apache 2.0) vía ONNX Runtime.<br>"
            "Fonemas: espeak-ng (GPL-3.0). IPA: CMU Pronouncing Dictionary.<br><br>"
            f"Atajo: {self.settings.hotkey}<br>Datos: {self.data_dir}"))

    def quit(self):
        self.stop()
        self.watcher.stop()
        self.engine.shutdown()
        self.misc.shutdown(wait=False, cancel_futures=True)
        self.tray.hide()
        self.app.quit()


def launch_command() -> str:
    exe = os.environ.get("POPSAY_LAUNCHER") or sys.executable
    return f'"{exe}"'


def main() -> int:
    if not winsys.acquire_single_instance():
        return 0  # ya está abierta (vive en la bandeja)
    QApplication.setQuitOnLastWindowClosed(False)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    data = paths.data_dir()
    setup_logging(data)
    log.info("Inicio %s %s (Python %s) en %s", APP_NAME, __version__, sys.version.split()[0], paths.app_dir())
    t0 = time.perf_counter()
    try:
        ctl = Controller(app)
    except FileNotFoundError as e:
        log.exception("Faltan archivos")
        QMessageBox.critical(None, APP_NAME, str(e))
        return 1
    log.info("UI lista en %.2f s", time.perf_counter() - t0)
    app.aboutToQuit.connect(lambda: ctl.watcher.stop())
    return app.exec()
