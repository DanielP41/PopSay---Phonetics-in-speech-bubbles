"""Motor de voz offline (Kokoro-82M vía ONNX Runtime) y reproductor de audio en streaming.

Velocidad:
- Modelo fp32: en CPU es ~2.3x más rápido que el int8 (las convoluciones cuantizadas son lentas en ONNX Runtime).
  Si no está, usa el int8.
- Síntesis por fragmentos: el primero es corto, suena apenas está listo y los demás se generan mientras tanto.
- Caché en memoria + caché en disco (data/cache): lo ya escuchado vuelve a sonar al instante, aun tras reiniciar.
"""

from __future__ import annotations

import hashlib
import logging
import math
import os
import threading
import time
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import paths

log = logging.getLogger(__name__)

SAMPLE_RATE = 24000


@dataclass
class Speech:
    audio: np.ndarray            # float32 mono, 24 kHz
    weights: list[float] | None  # fonemas por palabra (para el resaltado), si se pudieron alinear

    @property
    def duration(self) -> float:
        return len(self.audio) / SAMPLE_RATE


class DiskCache:
    """Audio ya sintetizado, en PCM de 16 bits. Se recorta por tamaño (los más viejos primero)."""

    def __init__(self, folder: Path, max_mb: int = 64):
        self.folder = folder
        self.max_bytes = max_mb * 1024 * 1024
        try:
            folder.mkdir(parents=True, exist_ok=True)
            self.ok = True
        except OSError:
            self.ok = False

    def _path(self, key: str) -> Path:
        return self.folder / f"{hashlib.sha1(key.encode('utf-8')).hexdigest()}.npz"

    def get(self, key: str) -> Speech | None:
        if not self.ok:
            return None
        p = self._path(key)
        try:
            with np.load(p) as z:
                audio = z["pcm"].astype(np.float32) / 32767
                w = z["weights"]
            os.utime(p)  # marca de uso para el recorte por antigüedad
            return Speech(audio, [float(x) for x in w] if len(w) else None)
        except (OSError, KeyError, ValueError):
            return None

    def put(self, key: str, speech: Speech) -> None:
        if not self.ok:
            return
        try:
            pcm = (np.clip(speech.audio, -1, 1) * 32767).astype(np.int16)
            tmp = self._path(key).with_suffix(".tmp.npz")
            np.savez(tmp, pcm=pcm, weights=np.asarray(speech.weights or [], dtype=np.float32))
            os.replace(tmp, self._path(key))
            self._trim()
        except OSError as e:
            log.debug("No pude guardar en la caché: %s", e)

    def _trim(self) -> None:
        files = sorted(self.folder.glob("*.npz"), key=lambda f: f.stat().st_mtime)
        total = sum(f.stat().st_size for f in files)
        while files and total > self.max_bytes:
            f = files.pop(0)
            total -= f.stat().st_size
            f.unlink(missing_ok=True)


class Engine:
    """Carga el modelo en segundo plano y sintetiza en un hilo propio (los pedidos se atienden en orden)."""

    def __init__(self, cache_items: int = 128, cache_dir: Path | None = None):
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tts")
        self._ready = threading.Event()
        self._error: Exception | None = None
        self._kokoro = None
        self._cache: OrderedDict[tuple, Speech] = OrderedDict()
        self._cache_items = cache_items
        self._disk = DiskCache(cache_dir) if cache_dir else None
        self._lock = threading.Lock()
        self.model_name = ""
        self.load_seconds = 0.0
        self._rtf = 0.7  # segundos de cálculo por segundo de audio; se mide al cargar y se ajusta con el uso

    def start(self) -> Future:
        return self._pool.submit(self._load)

    def _load(self) -> None:
        t0 = time.perf_counter()
        try:
            import onnxruntime as ort
            from kokoro_onnx import Kokoro

            model = paths.find_first(paths.MODEL_FILES)
            so = ort.SessionOptions()
            so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL  # hilos: ONNX Runtime usa los núcleos físicos
            session = ort.InferenceSession(str(model), so, providers=["CPUExecutionProvider"])
            self._kokoro = Kokoro.from_session(session, str(paths.find(paths.VOICES_FILE)))
            self.model_name = model.name
            # Precalentamiento: la primera inferencia es bastante más lenta.
            self._kokoro.create("Hi.", voice="af_heart", speed=1.0, lang="en-us")
            t1 = time.perf_counter()
            audio, _ = self._kokoro.create("This short sentence measures how fast this computer speaks.",
                                           voice="af_heart", speed=1.0, lang="en-us")
            self._rtf = (time.perf_counter() - t1) / max(0.5, len(audio) / SAMPLE_RATE)
            self.load_seconds = time.perf_counter() - t0
            log.info("Kokoro (%s) listo en %.1f s · rtf %.2f", model.name, self.load_seconds, self._rtf)
        except Exception as e:  # se informa en la UI
            log.exception("No pude cargar el modelo")
            self._error = e
        finally:
            self._ready.set()

    @property
    def rtf(self) -> float:
        """Velocidad del motor redondeada hacia arriba (0.1): planifica los fragmentos sin huecos."""
        return min(3.0, max(0.3, math.ceil(self._rtf * 10) / 10))

    @property
    def ready(self) -> bool:
        return self._ready.is_set() and self._error is None

    @property
    def error(self) -> Exception | None:
        return self._error

    def voices(self) -> list[str]:
        return list(self._kokoro.get_voices()) if self._kokoro else []

    def phonemize(self, text: str, accent: str) -> str:
        self._ready.wait()
        if not self._kokoro:
            return ""
        return self._kokoro.tokenizer.phonemize(text, "en-gb" if accent == "en-GB" else "en-us")

    def synthesize(self, text: str, voice: str, accent: str, speed: float) -> Future:
        return self._pool.submit(self._synth, text, voice, accent, float(speed))

    def cached(self, text: str, voice: str, accent: str, speed: float) -> Speech | None:
        """Consulta sólo la caché en memoria (sin esperar al hilo de síntesis)."""
        with self._lock:
            return self._cache.get((text, voice, accent, round(float(speed), 3)))

    def _synth(self, text: str, voice: str, accent: str, speed: float) -> Speech:
        self._ready.wait()
        if self._error:
            raise RuntimeError(f"El modelo de voz no cargó: {self._error}")
        key = (text, voice, accent, round(speed, 3))
        with self._lock:
            hit = self._cache.get(key)
            if hit:
                self._cache.move_to_end(key)
                return hit
        disk_key = f"{self.model_name}|{voice}|{accent}|{key[3]}|{text}"
        speech = self._disk.get(disk_key) if self._disk else None
        if speech is None:
            lang = "en-gb" if accent == "en-GB" else "en-us"
            t0 = time.perf_counter()
            audio, sr = self._kokoro.create(text, voice=voice, speed=speed, lang=lang)
            audio = np.asarray(audio, dtype=np.float32).ravel()
            assert sr == SAMPLE_RATE
            speech = Speech(audio=audio, weights=self._word_weights(text, lang))
            took = time.perf_counter() - t0
            if speech.duration > 1.0:  # si la compu anda más lenta (otra tarea pesada), los cortes se achican
                r = took / speech.duration
                self._rtf = r if r > self._rtf else 0.8 * self._rtf + 0.2 * r
            log.info("Síntesis %.2f s de audio en %.2f s (%d car.)", speech.duration, took, len(text))
            if self._disk and len(text) <= 300:
                self._disk.put(disk_key, speech)
        with self._lock:
            self._cache[key] = speech
            while len(self._cache) > self._cache_items:
                self._cache.popitem(last=False)
        return speech

    def _word_weights(self, text: str, lang: str) -> list[float] | None:
        """Fonemas por palabra: alinean el resaltado mejor que las sílabas."""
        try:
            words = text.split()
            ph = self._kokoro.tokenizer.phonemize(text, lang).split()
            if len(ph) != len(words):
                return None
            return [float(len(p.strip(".,;:!?\"'()").replace("ˈ", "").replace("ˌ", ""))) or 1.0 for p in ph]
        except Exception:
            return None

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)


class Player:
    """Reproduce audio con sounddevice en modo streaming: se puede ir agregando mientras suena."""

    def __init__(self):
        self._stream = None
        self._audio = np.zeros(0, dtype=np.float32)
        self._pos = 0
        self._closed = True
        self._lock = threading.Lock()
        self._on_finish = None
        self._gen = 0

    def play(self, audio: np.ndarray, on_finish=None, more: bool = False) -> None:
        """Empieza a sonar. Con more=True quedan fragmentos por venir: agregalos con append() y cerrá con close()."""
        import sounddevice as sd

        self.stop()
        with self._lock:
            self._audio = np.asarray(audio, dtype=np.float32).ravel()
            self._pos = 0
            self._closed = not more
            self._on_finish = on_finish
            self._gen += 1
            gen = self._gen

        def callback(outdata, frames, _time, _status):
            with self._lock:
                chunk = self._audio[self._pos : self._pos + frames]
                n = len(chunk)
                outdata[:n, 0] = chunk
                if n < frames:
                    outdata[n:] = 0
                    self._pos += n
                    if self._closed:
                        raise sd.CallbackStop
                    return  # falta el próximo fragmento: silencio breve hasta que llegue
                self._pos += n

        def finished():
            with self._lock:
                cb = self._on_finish if gen == self._gen else None
            if cb:
                cb()

        self._stream = sd.OutputStream(
            samplerate=SAMPLE_RATE, channels=1, dtype="float32", callback=callback, finished_callback=finished,
            latency="low",
        )
        self._stream.start()

    def append(self, audio: np.ndarray) -> None:
        with self._lock:
            self._audio = np.concatenate([self._audio, np.asarray(audio, dtype=np.float32).ravel()])

    def close(self) -> None:
        """No vienen más fragmentos: al terminar lo que queda, se dispara on_finish."""
        with self._lock:
            self._closed = True

    def stop(self) -> None:
        with self._lock:
            self._gen += 1  # el finished_callback del stream viejo queda sin efecto
            stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.abort()
                stream.close()
            except Exception:
                pass

    @property
    def position(self) -> int:
        """Muestras ya reproducidas."""
        with self._lock:
            return self._pos

    @property
    def progress(self) -> float:
        with self._lock:
            return 0.0 if not len(self._audio) else min(1.0, self._pos / len(self._audio))

    @property
    def playing(self) -> bool:
        return self._stream is not None and self._stream.active
