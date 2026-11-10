"""Prueba del agarre de tamaño de la extensión: arrastrar agranda la burbuja; doble clic la restablece."""
import http.server
import sys
import tempfile
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from smoke_e2e import EXT, H, find_nodes  # noqa: E402

OUT = Path(__file__).resolve().parent


def box(cdp, cls):
    n = find_nodes(cdp, cls)[0][0]
    q = cdp.send("DOM.getBoxModel", {"backendNodeId": n["backendNodeId"]})["model"]["border"]
    return q[0], q[1], q[4] - q[0], q[5] - q[1]


def main():
    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    exe = sys.argv[1] if len(sys.argv) > 1 else None
    ok = False
    with sync_playwright() as p, tempfile.TemporaryDirectory() as prof:
        ctx = p.chromium.launch_persistent_context(prof, executable_path=exe, headless=True, viewport={"width": 1100, "height": 700},
            args=["--headless=new", f"--disable-extensions-except={EXT}", f"--load-extension={EXT}", "--disable-features=DisableLoadExtensionCommandLineSwitch"])
        sw = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker")
        ctx.pages[0].wait_for_timeout(800)
        page = ctx.new_page(); page.goto(f"http://127.0.0.1:{srv.server_port}/"); page.wait_for_timeout(500)
        sel = """() => { const el = document.getElementById('s'); const r = document.createRange(); r.selectNodeContents(el);
                 const s = getSelection(); s.removeAllRanges(); s.addRange(r); const b = el.getBoundingClientRect();
                 el.dispatchEvent(new MouseEvent('mouseup', {bubbles: true, button: 0, clientX: b.right, clientY: b.bottom})); }"""
        page.evaluate(sel); page.wait_for_timeout(1000)
        cdp = ctx.new_cdp_session(page)
        before = box(cdp, "fb-card"); tb = box(cdp, "fb-text")
        gx, gy, gw, gh = box(cdp, "fb-grip")
        print("antes:   tarjeta %.0fx%.0f · texto %.0f px · agarre en (%.0f,%.0f)" % (before[2], before[3], tb[3], gx, gy))
        page.mouse.move(gx + gw / 2, gy + gh / 2); page.mouse.down()
        page.mouse.move(gx + gw / 2 + 160, gy + gh / 2 + 120, steps=8); page.mouse.up(); page.wait_for_timeout(300)
        after = box(cdp, "fb-card"); ta = box(cdp, "fb-text")
        print("después: tarjeta %.0fx%.0f · texto %.0f px" % (after[2], after[3], ta[3]))
        page.screenshot(path=str(OUT / "smoke_grip.png"))
        stored = sw.evaluate("chrome.storage.sync.get(['bubbleWidth','bubbleTextHeight'])")
        print("guardado:", stored)
        still_open = bool(find_nodes(cdp, "fb-card"))
        # se reabre con el tamaño guardado
        page.mouse.click(30, 650); page.wait_for_timeout(300); page.evaluate(sel); page.wait_for_timeout(900)
        reopened = box(cdp, "fb-card")
        print("reabierta: %.0f de ancho" % reopened[2])
        gx, gy, gw, gh = box(cdp, "fb-grip"); page.mouse.dblclick(gx + gw / 2, gy + gh / 2); page.wait_for_timeout(300)
        reset = box(cdp, "fb-card")
        print("doble clic: %.0f de ancho · guardado %s" % (reset[2], sw.evaluate("chrome.storage.sync.get(['bubbleWidth'])")))
        # el alto elegido es un máximo: con texto corto el cuadro sigue ajustado al contenido
        ok = (still_open and after[2] - before[2] > 140 and abs(ta[3] - tb[3]) < 2 and stored.get("bubbleTextHeight", 0) > tb[3] and stored.get("bubbleWidth", 0) > 0
              and abs(reopened[2] - after[2]) < 2 and abs(reset[2] - before[2]) < 2)
        ctx.close()
    srv.shutdown()
    print("OK" if ok else "FALLÓ"); sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
