"""Smoke test E2E de la extensión SIN INTERNET: carga la extensión en Chrome, corta la red,
selecciona texto, toca Play y verifica que la voz incluida suene (estado 'playing' -> 'idle')
y que el IPA salga del diccionario incluido.

Uso: python tools/smoke_e2e.py [ruta_chrome]
"""
import http.server
import sys
import tempfile
import threading
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
EXT = ROOT / "extension"
OUT = ROOT / "tools"

PAGE = b"""<!doctype html><html><head><meta charset=utf-8><title>Demo</title>
<style>body{font:18px/1.6 Georgia,serif;max-width:640px;margin:60px auto;background:#fff;color:#222}</style></head>
<body><h1>Code review</h1>
<p id=t>Before merging, <span id=s>required reviews validate approach</span> and catch regressions early.</p>
<p>Some more text below so there is room for the bubble.</p></body></html>"""


class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(PAGE)

    def log_message(self, *a):
        pass


def find_nodes(cdp, cls):
    """Busca nodos por clase atravesando shadow roots cerrados (el CDP los expone)."""
    root = cdp.send("DOM.getDocument", {"depth": -1, "pierce": True})["root"]
    hits = []

    def walk(n):
        attrs = n.get("attributes", [])
        d = dict(zip(attrs[::2], attrs[1::2]))
        if cls in d.get("class", "").split():
            hits.append((n, d))
        for key in ("children", "shadowRoots"):
            for c in n.get(key, []):
                walk(c)

    walk(root)
    return hits


def text_of(node):
    out = []

    def walk(n):
        if n.get("nodeType") == 3:
            out.append(n.get("nodeValue", ""))
        for c in n.get("children", []):
            walk(c)

    walk(node)
    return "".join(out)


def main():
    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_port}/"
    exe = sys.argv[1] if len(sys.argv) > 1 else None
    ok = False
    with sync_playwright() as p, tempfile.TemporaryDirectory() as profile:
        ctx = p.chromium.launch_persistent_context(
            profile,
            executable_path=exe,
            headless=True,
            args=[
                "--headless=new",
                f"--disable-extensions-except={EXT}",
                f"--load-extension={EXT}",
                "--disable-features=DisableLoadExtensionCommandLineSwitch",
                "--autoplay-policy=no-user-gesture-required",
            ],
            viewport={"width": 900, "height": 520},
        )
        sw = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker", timeout=10000)
        print("service worker:", sw.url)
        ctx.pages[0].wait_for_timeout(1000)  # deja abrir la página de opciones del primer arranque
        page = ctx.new_page()
        page.goto(url)
        page.wait_for_timeout(600)
        ctx.set_offline(True)
        print("red cortada: offline =", page.evaluate("navigator.onLine === false"))

        page.evaluate(
            """() => {
              const el = document.getElementById('s');
              const r = document.createRange(); r.selectNodeContents(el);
              const sel = getSelection(); sel.removeAllRanges(); sel.addRange(r);
              const b = el.getBoundingClientRect();
              el.dispatchEvent(new MouseEvent('mouseup', {bubbles: true, button: 0, clientX: b.right, clientY: b.bottom}));
            }"""
        )
        page.wait_for_timeout(1200)
        cdp = ctx.new_cdp_session(page)
        play = find_nodes(cdp, "fb-play")
        if not play:
            print("FALLÓ: no apareció la burbuja")
            sys.exit(1)
        box = cdp.send("DOM.getBoxModel", {"backendNodeId": play[0][0]["backendNodeId"]})["model"]["content"]
        cx, cy = (box[0] + box[4]) / 2, (box[1] + box[5]) / 2
        t0 = time.monotonic()
        page.mouse.click(cx, cy)

        states, playing_at, shot_taken = [], None, False
        while time.monotonic() - t0 < 90:
            card = find_nodes(cdp, "fb-card")
            st = card[0][1].get("data-state") if card else "closed"
            if not states or states[-1][1] != st:
                states.append((round(time.monotonic() - t0, 1), st))
            if st == "playing" and playing_at is None:
                playing_at = time.monotonic() - t0
            if st == "playing" and not shot_taken and time.monotonic() - t0 > (playing_at or 0) + 0.6:
                page.screenshot(path=str(OUT / "smoke_playing.png"))
                shot_taken = True
            if playing_at is not None and st == "idle":
                break
            page.wait_for_timeout(150)

        ipa = find_nodes(cdp, "fb-ipa")
        ipa_text = text_of(ipa[0][0]) if ipa else ""
        notice = find_nodes(cdp, "fb-notice")
        print("estados:", states)
        print("IPA:", ipa_text)
        print("aviso:", text_of(notice[0][0]) if notice else "")
        page.screenshot(path=str(OUT / "smoke.png"))
        ok = playing_at is not None and states[-1][1] == "idle" and "ˈ" in ipa_text
        ctx.close()
    srv.shutdown()
    print("OK" if ok else "FALLÓ")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
