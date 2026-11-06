"""Autodiagnóstico: python -m popsay.selftest  (lo corre Diagnostico.bat y el CI de Windows)."""

from __future__ import annotations

import os
import sys
import time
import traceback


def check(name, fn):
    t0 = time.perf_counter()
    try:
        detail = fn()
        print(f"[OK]    {name} ({time.perf_counter() - t0:.1f} s) {detail or ''}")
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[FALLA] {name}: {e}")
        traceback.print_exc(limit=3)
        return False


def main() -> int:
    from . import __version__, paths

    print(f"PopSay {__version__} — Python {sys.version.split()[0]} — {sys.platform}")
    print(f"Carpeta: {paths.app_dir()}")
    results = []

    def files():
        return ", ".join(f"{n}" for n in (paths.find_first(paths.MODEL_FILES).name, paths.find(paths.VOICES_FILE).name,
                                          paths.find(paths.IPA_FILE).name, paths.find(paths.VOICES_JSON).name))

    results.append(check("Archivos del modelo", files))

    from .engine import Engine

    eng = Engine()

    def load():
        eng.start().result()
        if eng.error:
            raise eng.error
        return f"voces: {len(eng.voices())}"

    results.append(check("Cargar Kokoro + espeak-ng", load))

    def synth():
        out = []
        for voice, acc in (("af_heart", "en-US"), ("bf_emma", "en-GB")):
            t0 = time.perf_counter()
            s = eng.synthesize("Required reviews validate approach.", voice, acc, 1.0).result()
            if s.duration < 1.0 or float(abs(s.audio).max()) < 0.01:
                raise RuntimeError("audio vacío")
            out.append(f"{acc}: {s.duration:.1f} s de audio en {time.perf_counter() - t0:.1f} s")
        return "; ".join(out)

    results.append(check("Sintetizar voz (US y UK)", synth))

    def ipa():
        from .ipa import IpaDictionary

        r = IpaDictionary(paths.find(paths.IPA_FILE), eng.phonemize).transcribe("approach Kubernetes", "en-US")
        if "əˈproʊtʃ" not in r.ipa:
            raise RuntimeError(r.ipa)
        return r.ipa

    results.append(check("IPA offline", ipa))

    def audio():
        import sounddevice as sd

        dev = sd.query_devices(kind="output")
        return dev["name"]

    results.append(check("Dispositivo de audio", audio))

    def qt():
        from PySide6.QtWidgets import QApplication

        from .settings import Settings
        from .ui import Bubble

        app = QApplication.instance() or QApplication(sys.argv)
        b = Bubble(Settings())
        b.show_text("Hello world", b.pos())
        b.grab()
        b.hide()
        return f"plataforma Qt: {app.platformName()}"

    results.append(check("Interfaz Qt", qt))

    if sys.platform == "win32":
        from . import winsys

        def hotkey():
            import ctypes

            mods, vk = winsys.parse_hotkey("Alt+Shift+P")
            ok = winsys.user32.RegisterHotKey(None, 0xBEEF, mods | 0x4000, vk)
            if ok:
                winsys.user32.UnregisterHotKey(None, 0xBEEF)
                return "Alt+Shift+P libre"
            return f"Alt+Shift+P ocupado por otra app (error {ctypes.get_last_error()}); la app abierta puede ser ella"

        results.append(check("Atajo global", hotkey))
        results.append(check("Portapapeles", lambda: f"{len(winsys.get_clipboard_text() or '')} caracteres de texto"))

    eng.shutdown()
    ok = all(results)
    print("\nTODO BIEN" if ok else "\nHAY PROBLEMAS: copiá este texto para revisarlo")
    return 0 if ok else 1


if __name__ == "__main__":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    raise SystemExit(main())
