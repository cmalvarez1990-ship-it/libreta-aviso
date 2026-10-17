"""
Avisa por Telegram cuando hay hora libre para renovar la libreta de conducir
(Intendencia de Montevideo). Revisa cada 5 minutos.

Uso:
    export TELEGRAM_TOKEN="..."
    export TELEGRAM_CHAT_ID="..."
    python3 libreta_watcher.py --debug   # una sola vez, con navegador visible
    python3 libreta_watcher.py           # loop cada 5 minutos
"""
import json
import os
import re
import sys
import time
import traceback

import requests
from playwright.sync_api import sync_playwright

URL = (
    "https://www.montevideo.gub.uy/sae/agendarReserva/Paso1.xhtml"
    "?agenda=RENOVCON&recurso=RENCOMUN"
    "&pagina_retorno=https%3A//www.montevideo.gub.uy&solo_cuerpo=false"
)
INTERVAL = 300  # segundos
TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
DEBUG = "--debug" in sys.argv

# Si necesitás otra categoría que la que viene por defecto ("Categorías A-G1-G2"),
# escribí acá el texto EXACTO de la opción, por ejemplo: "Categorías B". 
# Dejalo vacío ("") para usar la que viene por defecto.
TRAMITE = ""

# Cuántos meses mirar (1 = solo el mes que muestra la página, 2 = ese y el siguiente).
MESES = 2

# JavaScript que lee todos los días del calendario con su color.
JS_DIAS = """
() => {
  const tablas = [...document.querySelectorAll('table')].filter(t =>
    /\\blun\\b/i.test(t.innerText) && /\\bdom\\b/i.test(t.innerText));
  if (!tablas.length) return {mes: '', dias: [], error: 'no calendario'};
  // la tabla más interna que contiene el calendario
  const t = tablas[tablas.length - 1];
  const mes = (t.innerText.match(/[a-záéíóúñ]+,\\s*\\d{4}/i) || [''])[0];
  const dias = [];
  t.querySelectorAll('td').forEach(td => {
    const txt = td.innerText.trim();
    if (!/^\\d{1,2}$/.test(txt)) return;
    const el = td.querySelector('a, span') || td;
    const cs = getComputedStyle(td), ce = getComputedStyle(el);
    dias.push({
      dia: txt, cls: td.className, clsEl: el.className,
      bg: cs.backgroundColor, color: ce.color,
      link: !!td.querySelector('a'), onclick: !!(td.getAttribute('onclick') || el.getAttribute('onclick')),
    });
  });
  return {mes, dias};
}
"""


def es_verde(css: str) -> bool:
    m = re.findall(r"\d+(?:\.\d+)?", css or "")
    if len(m) < 3:
        return False
    r, g, b = (float(x) for x in m[:3])
    if len(m) >= 4 and float(m[3]) == 0:
        return False
    return g > 90 and g > r * 1.3 and g > b * 1.3


def notify(text: str) -> None:
    r = requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        data={"chat_id": CHAT_ID, "text": text, "disable_web_page_preview": "true"},
        timeout=15,
    )
    r.raise_for_status()


def revisar(page):
    print("Abriendo la página...", flush=True)
    page.goto(URL, wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(4000)

    sel = page.locator("select").first
    if DEBUG and sel.count():
        opciones = sel.locator("option").all_inner_texts()
        print("Trámites disponibles:", opciones, flush=True)
    if TRAMITE and sel.count():
        sel.select_option(label=TRAMITE)
        page.wait_for_timeout(4000)

    libres = []
    ultimo_mes = None
    for i in range(MESES):
        if i > 0:
            sig = page.get_by_text(">", exact=True).first
            if not sig.count():
                print("No encontré el botón para pasar al mes siguiente.", flush=True)
                break
            sig.click()
            page.wait_for_timeout(4000)
        data = page.evaluate(JS_DIAS)
        if data["mes"] == ultimo_mes:
            print("El calendario no cambió de mes.", flush=True)
            break
        ultimo_mes = data["mes"]
        if DEBUG:
            page.screenshot(path=f"debug_calendario_{i + 1}.png", full_page=True)
            vistos = {}
            for d in data["dias"]:
                clave = (d["cls"], d["clsEl"], d["bg"], d["color"], d["link"], d["onclick"])
                vistos.setdefault(clave, []).append(d["dia"])
            print("Mes:", data["mes"], flush=True)
            for clave, dias in vistos.items():
                print(" ", clave, "->", ",".join(dias), flush=True)
        for d in data["dias"]:
            if es_verde(d["color"]) or es_verde(d["bg"]):
                libres.append(f"{d['dia']} ({data['mes']})")
    return libres


STATE_FILE = "state.json"
ONCE = "--once" in sys.argv


def cargar_estado() -> set[str]:
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return set(json.load(f))
    except Exception:
        return set()


def guardar_estado(dias: set[str]) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(dias), f)


def aviso(actuales: set[str]) -> None:
    notify("🚗 Hay lugar para renovar la libreta. Días libres: "
           + ", ".join(sorted(actuales)) + f"\n{URL}")


def main() -> None:
    if not DEBUG and (not TOKEN or not CHAT_ID):
        sys.exit("Faltan TELEGRAM_TOKEN y/o TELEGRAM_CHAT_ID")

    # Modo "una vez" (para GitHub Actions): revisa, avisa si hay novedades y termina.
    # El estado (de qué días ya avisó) se guarda en state.json entre ejecuciones.
    ultimo: set[str] = cargar_estado() if ONCE else set()
    print("Arrancando...", flush=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not DEBUG)
        while True:
            try:
                page = browser.new_page(viewport={"width": 1280, "height": 900})
                libres = revisar(page)
                page.close()
                actuales = set(libres)
                print(time.strftime("%H:%M:%S"), "días libres:", sorted(actuales) or "ninguno", flush=True)
                if actuales - ultimo and not DEBUG:
                    aviso(actuales)
                ultimo = actuales
                if ONCE:
                    guardar_estado(actuales)
            except Exception:
                traceback.print_exc()
                if ONCE:
                    browser.close()
                    sys.exit(1)
            if DEBUG or ONCE:
                break
            time.sleep(INTERVAL)
        browser.close()


if __name__ == "__main__":
    main()
