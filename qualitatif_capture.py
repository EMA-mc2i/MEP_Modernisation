# -*- coding: utf-8 -*-
"""Capture les requêtes OD (estimated-timetable?LineRef=...) et SM arrêt (stop-monitoring)
pour chaque réseau échantillonné (echantillons.json, voir qualitatif_echantillons.py).

La requête ALL n'est pas recapturée ici : elle existe déjà dans releves/ (verifier_criteres_rollback.py).

Usage :
    py -I qualitatif_capture.py avant     # à lancer maintenant (Legacy)
    py -I qualitatif_capture.py apres     # à lancer après la MEP, sur demande d'Erwan (NG)
Sortie : captures/<avant|apres>/<réseau>__OD.json et __SM.json, + captures/<avant|apres>/_horodatage.txt
"""
import datetime
import json
import pathlib
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
CAPTURES = HERE / "captures"
BASE = "https://prim.iledefrance-mobilites.fr/marketplace"
THROTTLE = 0.3

sys.path.insert(0, str(ROOT / "13_Dashboards_IA" / "commun"))
import cle_api  # noqa: E402

API_KEY = cle_api.lire_cle()


def fetch(path, params):
    q = urllib.parse.urlencode(params)
    req = urllib.request.Request(f"{BASE}/{path}?{q}", headers={"apikey": API_KEY, "Accept": "application/json"})
    for tentative in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429 and tentative < 2:
                time.sleep(3 * (tentative + 1))
                continue
            return {"_erreur": f"HTTP {e.code}"}
        except Exception as e:
            return {"_erreur": str(e)}


def nom_fichier(clef):
    """Un nom de réseau peut contenir des caractères invalides sur disque (ex. "MeC_Bus_PC · X")."""
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in clef)


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ("avant", "apres"):
        raise SystemExit("Usage : py -I qualitatif_capture.py avant|apres")
    phase = sys.argv[1]

    echantillons = json.loads((HERE / "echantillons.json").read_text(encoding="utf-8"))
    dossier = CAPTURES / phase
    dossier.mkdir(parents=True, exist_ok=True)

    n_ok = n_err = 0
    for clef, e in echantillons["reseaux"].items():
        if not e.get("trouve"):
            continue
        base = nom_fichier(clef)
        od = fetch("estimated-timetable", {"LineRef": f"STIF:Line::{e['code']}:"})
        (dossier / f"{base}__OD.json").write_text(json.dumps(od, ensure_ascii=False), encoding="utf-8")
        time.sleep(THROTTLE)
        sm = fetch("stop-monitoring", {"MonitoringRef": e["monitoring_ref"]})
        (dossier / f"{base}__SM.json").write_text(json.dumps(sm, ensure_ascii=False), encoding="utf-8")
        time.sleep(THROTTLE)
        erreur = ("_erreur" in od) or ("_erreur" in sm)
        n_err += erreur
        n_ok += not erreur
        print(("ERREUR " if erreur else "ok     ") + clef)

    (dossier / "_horodatage.txt").write_text(datetime.datetime.now(datetime.timezone.utc).isoformat(), encoding="utf-8")
    print(f"\n{n_ok} réseaux capturés, {n_err} en erreur -> {dossier}")


if __name__ == "__main__":
    main()
