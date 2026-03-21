import requests
from bs4 import BeautifulSoup
import json
import os
import re
import subprocess
from datetime import datetime
from urllib.parse import quote

# ─────────────────────────────────────────────
#  CONFIGURACIÓN
# ─────────────────────────────────────────────
URL = "https://ipfs.io/ipns/k51qzi5uqu5dgkcvogpofvprp6i8i2rtr6ej0li3ueqimgxw3803gjoqhar1uw/"
PREV_FILE  = "ids_prev.json"
M3U_FILE   = "playlist.m3u"
LOG_FILE   = "scraper.log"

# URL base de la guía EPG
EPG_URL = "https://raw.githubusercontent.com/davidmuma/EPG_dobleM/master/guiatv.xml"

# URL base de los logos (picons de davidmuma)
PICONS_BASE = "https://raw.githubusercontent.com/davidmuma/picons_dobleM/master/icon"

# URL de Acestream Engine local
ACE_HOST = "http://127.0.0.1:6878"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    )
}

# ─────────────────────────────────────────────
#  MAPA DE LOGOS: nombre_canal → nombre_picon
#  Añade o ajusta entradas según el repo de picons
#  https://github.com/davidmuma/picons_dobleM/tree/master/icon
# ─────────────────────────────────────────────
LOGO_MAP = {
    "MOVISTAR +"            : "Movistar+",
    "MOVISTAR LALIGA"       : "Movistar LaLiga",
    "DAZN LALIGA"           : "DAZN LaLiga",
    "LALIGA HYPERMOTION"    : "LaLiga Hypermotion",
    "DAZN F1"               : "DAZN F1",
    "DAZN MOTOGP"           : "DAZN MotoGP",
    "EVENTUAL (SOLO EVENTOS)": "Evento",
}

def logo_url(canal_nombre):
    """Devuelve la URL del logo desde el repo de picons de davidmuma."""
    picon_name = LOGO_MAP.get(canal_nombre, canal_nombre)
    return f"{PICONS_BASE}/{quote(picon_name)}.png"

# ─────────────────────────────────────────────
#  LOGGING
# ─────────────────────────────────────────────
def log(msg):
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")

# ─────────────────────────────────────────────
#  SCRAPING
# ─────────────────────────────────────────────
def scrape_ids():
    """
    Extrae de la web:
      - IDs Acestream (40 hex) → sección SHICKAT ACESTREAM
      - Códigos MyLinkPaste (24 hex) → sección CÓDIGOS MYLINKPASTE
    Devuelve dict con nombre, id, sección e idiomas.
    """
    resp = requests.get(URL, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    acestream_channels = []   # lista de dicts
    mylinkpaste_channels = [] # lista de dicts

    ace_pat = re.compile(r'^[0-9a-f]{40}$', re.IGNORECASE)
    mlp_pat = re.compile(r'^[0-9a-f]{24}$', re.IGNORECASE)

    for section in soup.find_all("section", class_="category-section"):
        heading_el = section.find(["h1","h2","h3","h4"])
        section_name = heading_el.get_text(strip=True) if heading_el else "SIN SECCIÓN"

        for article in section.find_all("article"):
            nombre_el = article.find(class_="canal-nombre")
            nombre = nombre_el.get_text(strip=True) if nombre_el else "DESCONOCIDO"
            lang = article.get("data-lang", "")

            # Buscar IDs en texto de los <a>
            for a in article.find_all("a"):
                text = a.get_text(strip=True)

                if ace_pat.match(text):
                    acestream_channels.append({
                        "nombre"  : nombre,
                        "id"      : text,
                        "seccion" : section_name,
                        "lang"    : lang
                    })

                elif mlp_pat.match(text):
                    mylinkpaste_channels.append({
                        "nombre"  : nombre,
                        "id"      : text,
                        "seccion" : section_name,
                        "lang"    : lang
                    })

    return {
        "acestream"   : acestream_channels,
        "mylinkpaste" : mylinkpaste_channels,
        "timestamp"   : datetime.utcnow().isoformat()
    }

# ─────────────────────────────────────────────
#  PERSISTENCIA
# ─────────────────────────────────────────────
def load_previous():
    if os.path.exists(PREV_FILE):
        with open(PREV_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"acestream": [], "mylinkpaste": []}

def save_current(data):
    with open(PREV_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

# ─────────────────────────────────────────────
#  DETECCIÓN DE CAMBIOS
# ─────────────────────────────────────────────
def detect_changes(old, new):
    changes = {}

    for section in ["acestream", "mylinkpaste"]:
        old_map = {c["nombre"]: c["id"] for c in old.get(section, [])}
        new_map = {c["nombre"]: c["id"] for c in new.get(section, [])}

        added   = {k: v for k, v in new_map.items() if k not in old_map}
        removed = {k: v for k, v in old_map.items() if k not in new_map}
        changed = {k: {"old": old_map[k], "new": new_map[k]}
                   for k in new_map if k in old_map and old_map[k] != new_map[k]}

        if added or removed or changed:
            changes[section] = {"added": added, "removed": removed, "changed": changed}

    return changes, bool(changes)

# ─────────────────────────────────────────────
#  GENERACIÓN DEL M3U
# ─────────────────────────────────────────────
def generate_m3u(data):
    ts = data.get("timestamp", "")
    lines = [
        f'#EXTM3U x-tvg-url="{EPG_URL}"',
        f"",
        f"# Wiki Shickat — generado automáticamente",
        f"# Última actualización: {ts}",
        f"",
    ]

    # ── Canales Acestream ──────────────────────
    for ch in data["acestream"]:
        nombre  = ch["nombre"]
        ace_id  = ch["id"]
        seccion = ch.get("seccion", "Acestream")
        logo    = logo_url(nombre)
        url     = f"{ACE_HOST}/ace/manifest.m3u8?id={ace_id}"

        lines.append(
            f'#EXTINF:-1 tvg-id="{nombre}" '
            f'tvg-logo="{logo}" '
            f'group-title="{seccion}",'
            f'{nombre}'
        )
        lines.append(url)
        lines.append("")

    # ── Códigos MyLinkPaste (como referencia) ──
    for ch in data["mylinkpaste"]:
        nombre  = ch["nombre"]
        mlp_id  = ch["id"]
        seccion = ch.get("seccion", "MyLinkPaste")
        logo    = logo_url(nombre)

        lines.append(
            f'#EXTINF:-1 tvg-id="{nombre}" '
            f'tvg-logo="{logo}" '
            f'group-title="{seccion}",'
            f'{nombre}'
        )
        # MyLinkPaste no tiene URL de stream directa,
        # se deja el ID como referencia comentada
        lines.append(f"# MyLinkPaste ID: {mlp_id}")
        lines.append("")

    with open(M3U_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    log(f"[✓] {M3U_FILE} generado: "
        f"{len(data['acestream'])} Acestream, "
        f"{len(data['mylinkpaste'])} MyLinkPaste")

# ─────────────────────────────────────────────
#  GIT PUSH
# ─────────────────────────────────────────────
def git_push():
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    cmds = [
        ["git", "add", M3U_FILE, PREV_FILE],
        ["git", "commit", "-m", f"Actualización automática {ts}"],
        ["git", "push"],
    ]
    for cmd in cmds:
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0 and "nothing to commit" not in r.stdout + r.stderr:
            log(f"[!] Error git ({' '.join(cmd)}): {r.stderr.strip()}")
            return False
    log("[✓] Cambios subidos a GitHub.")
    return True

# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────
def main():
    log("──────────────────────────────────────")
    log("[→] Iniciando scraper...")

    try:
        current = scrape_ids()
    except Exception as e:
        log(f"[✗] Error al scrapear: {e}")
        return

    log(f"[✓] Encontrados: {len(current['acestream'])} Acestream, "
        f"{len(current['mylinkpaste'])} MyLinkPaste")

    previous = load_previous()
    changes, has_changes = detect_changes(previous, current)

    if has_changes:
        log("[!] Cambios detectados:")
        for section, diff in changes.items():
            for k, v in diff.get("added", {}).items():
                log(f"    + NUEVO [{section}] {k}: {v}")
            for k, v in diff.get("removed", {}).items():
                log(f"    - ELIMINADO [{section}] {k}: {v}")
            for k, v in diff.get("changed", {}).items():
                log(f"    ~ CAMBIADO [{section}] {k}: {v['old']} → {v['new']}")

        generate_m3u(current)
        save_current(current)
        git_push()
    else:
        log("[=] Sin cambios. M3U no modificado.")
        # Actualiza solo el timestamp del JSON
        save_current(current)

if __name__ == "__main__":
    main()
