"""Arma la carpeta portable de Windows (se puede correr desde Linux, macOS o Windows).

    PopSay/
      PopSay.exe      lanzador (sin consola)
      Diagnostico.bat          igual, pero mostrando errores en una consola
      LEEME.txt
      runtime/                 Python 3.12 autónomo + paquetes de Windows
      app/                     el programa (popsay/ + run.pyw)
      models/                  Kokoro int8, voces en inglés, diccionario IPA
      data/                    ajustes y registro (se crea al usar)

Requisitos (sólo para armar): internet una vez, pip, y `pip install ziglang` para el lanzador.
Uso:  python tools/build_portable_windows.py
"""

from __future__ import annotations

import fnmatch
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
OUT = DIST / "PopSay"
CACHE = DIST / ".cache"

PBS_TAG, PY_VERSION = "20251120", "3.12.12"
PBS_URL = (f"https://github.com/astral-sh/python-build-standalone/releases/download/{PBS_TAG}/"
           f"cpython-{PY_VERSION}+{PBS_TAG}-x86_64-pc-windows-msvc-install_only_stripped.tar.gz")
PACKAGES = ["kokoro-onnx==0.6.1", "onnxruntime>=1.20", "numpy>=2", "sounddevice>=0.5", "PySide6-Essentials==6.12.0",
            "pyphen>=0.15"]

# Partes de PySide6 que la app no usa (QML, Quick, Designer, herramientas). QtCore/QtGui/QtWidgets quedan intactos.
QT_PRUNE = [
    "qml", "translations", "metatypes", "typesystems", "include", "glue", "scripts", "resources", "doc",
    "Qt6Quick*.dll", "Qt6Qml*.dll", "Qt6Designer*.dll", "Qt6LabsStyleKit*.dll", "Qt6UiTools.dll", "Qt6OpenGL*.dll",
    "Qt6Pdf*.dll", "Qt6ShaderTools.dll", "opengl32sw.dll", "Qt6Concurrent.dll", "Qt6Test.dll", "Qt6Sql.dll", "Qt6Xml.dll",
    "QtQuick*.pyd", "QtQml*.pyd", "QtOpenGL*.pyd", "QtDesigner.pyd", "QtUiTools.pyd", "QtTest.pyd", "QtSql.pyd",
    "QtConcurrent.pyd", "QtXml.pyd", "QtHelp.pyd", "Qt6Help.dll", "QtPrintSupport.pyd", "QtSvgWidgets.pyd", "*.exe", "*.pyi",
    "plugins/qmltooling", "plugins/qmllint", "plugins/qmlls", "plugins/designer", "plugins/sqldrivers",
    "plugins/networkinformation", "plugins/tls", "plugins/generic", "plugins/platforminputcontexts",
    "plugins/scenegraph", "plugins/position", "plugins/sensors", "plugins/texttospeech",
    "QtLabs*.pyd", "plugins/imageformats/qpdf.dll", "plugins/vectorimageformats",
]


def download(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        return dest
    print(f"Bajando {url.rsplit('/', 1)[-1]}…")
    tmp = dest.with_suffix(dest.suffix + ".part")
    urllib.request.urlretrieve(url, tmp)
    tmp.replace(dest)
    return dest


def build_runtime() -> Path:
    archive = download(PBS_URL, CACHE / PBS_URL.rsplit("/", 1)[-1])
    rt = OUT / "runtime"
    with tarfile.open(archive) as tar:
        tar.extractall(OUT, filter="data")
    (OUT / "python").rename(rt)
    # Sin pip, IDLE, tests ni tkinter: no se usan y ocupan espacio.
    for rel in ["Lib/site-packages/pip", "Lib/idlelib", "Lib/test", "Lib/tkinter", "Lib/turtledemo", "tcl",
                "Lib/ensurepip", "Scripts"]:
        shutil.rmtree(rt / rel, ignore_errors=True)
    for p in (rt / "Lib/site-packages").glob("pip-*"):
        shutil.rmtree(p, ignore_errors=True)
    return rt


def install_packages(rt: Path) -> None:
    site = rt / "Lib" / "site-packages"
    cmd = [sys.executable, "-m", "pip", "install", "--quiet", "--no-compile", "--target", str(site),
           "--platform", "win_amd64", "--python-version", PY_VERSION.rsplit(".", 1)[0], "--implementation", "cp",
           "--only-binary=:all:", *PACKAGES]
    print("Instalando paquetes de Windows…")
    subprocess.run(cmd, check=True)
    shutil.rmtree(site / "bin", ignore_errors=True)

    qt = site / "PySide6"
    removed = 0
    for pattern in QT_PRUNE:
        for p in list(qt.glob(pattern)) if any(ch in pattern for ch in "*?") else [qt / pattern]:
            if p.is_dir():
                removed += sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
                shutil.rmtree(p)
            elif p.is_file():
                removed += p.stat().st_size
                p.unlink()
    print(f"PySide6 recortado: {removed / 1e6:.0f} MB menos")

    # onnxruntime.dll necesita el runtime de C++ de Microsoft. Lo llevamos junto a python.exe
    # (PySide6 ya trae esas DLL redistribuibles) para no depender de que esté instalado.
    for dll in qt.glob("msvcp140*.dll"):
        shutil.copy2(dll, rt / dll.name)
    for dll in qt.glob("vcruntime140*.dll"):
        if not (rt / dll.name).exists():
            shutil.copy2(dll, rt / dll.name)
    for d in site.rglob("__pycache__"):
        shutil.rmtree(d, ignore_errors=True)


def copy_app() -> None:
    app = OUT / "app"
    shutil.copytree(ROOT / "desktop" / "popsay", app / "popsay", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copy2(ROOT / "desktop" / "run.pyw", app / "run.pyw")
    models = OUT / "models"
    models.mkdir()
    for src in [ROOT / "core/models/kokoro-v1.0.onnx", ROOT / "core/voices/voices-en.npz",
                ROOT / "core/voices/voices.json", ROOT / "core/ipa/ipa-en.json"]:
        if not src.exists():
            sys.exit(f"Falta {src}. Corré primero: python tools/build_core.py")
        shutil.copy2(src, models / src.name)
    (OUT / "data").mkdir()


def build_launcher() -> None:
    src = ROOT / "desktop" / "launcher"
    shutil.copy2(ROOT / "desktop/popsay/assets/icon.ico", src / "icon.ico")
    exe = OUT / "PopSay.exe"
    cmd = [sys.executable, "-m", "ziglang", "cc", "-target", "x86_64-windows-gnu", "-municode", "-DUNICODE", "-D_UNICODE",
           "-Wl,--subsystem,windows", "-Os", "-s", str(src / "launcher.c"), str(src / "launcher.rc"), "-o", str(exe)]
    try:
        subprocess.run(cmd, check=True, cwd=src)
    except (subprocess.CalledProcessError, FileNotFoundError):
        sys.exit("No pude compilar el lanzador. Instalá zig con:  pip install ziglang")
    for junk in OUT.glob("PopSay.pdb"):
        junk.unlink()

    (OUT / "Diagnostico.bat").write_text(
        "@echo off\r\n"
        "rem Abre PopSay mostrando los errores en esta ventana.\r\n"
        "cd /d \"%~dp0\"\r\n"
        "set POPSAY_LAUNCHER=%~dp0PopSay.exe\r\n"
        "chcp 65001 >nul\r\n"
        "echo === Autodiagnostico ===\r\n"
        "pushd \"%~dp0app\" & \"%~dp0runtime\\python.exe\" -E -s -X utf8 -m popsay.selftest & popd\r\n"
        "echo.\r\necho === Abriendo la app (cerrala desde la bandeja para volver aca) ===\r\n"
        "\"%~dp0runtime\\python.exe\" -E -s -X utf8 \"%~dp0app\\run.pyw\"\r\n"
        "echo.\r\necho (Si hubo un error, copialo de arriba o mirá data\\log.txt)\r\npause\r\n",
        encoding="utf-8",
    )
    shutil.copy2(ROOT / "desktop" / "LEEME.txt", OUT / "LEEME.txt")


def make_zip() -> Path:
    zpath = DIST / "PopSay-windows-portable.zip"
    zpath.unlink(missing_ok=True)
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for f in sorted(OUT.rglob("*")):
            if f.is_file():
                z.write(f, f.relative_to(DIST).as_posix())
    return zpath


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    rt = build_runtime()
    install_packages(rt)
    copy_app()
    build_launcher()
    size = sum(f.stat().st_size for f in OUT.rglob("*") if f.is_file())
    z = make_zip()
    print(f"Carpeta: {OUT} ({size / 1e6:.0f} MB)")
    print(f"Zip:     {z} ({z.stat().st_size / 1e6:.0f} MB)")


if __name__ == "__main__":
    main()
