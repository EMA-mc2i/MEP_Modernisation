# -*- coding: utf-8 -*-
"""Sélection des échantillons (ligne + arrêt) pour l'onglet Qualitatif, par réseau déjà
identifié dans le relevé quantitatif (verifier_criteres_rollback.py).

Pour chaque réseau, on prend sa ligne la plus fréquentée (nb de courses du dernier relevé),
puis on cherche dans le GTFS (open data IDFM, téléchargement direct, pas l'API Navitia —
voir la remarque ci-dessous) un passage théorique proche de l'heure cible (22h le 01/12/2026
par défaut, la MEP), pour pouvoir capturer "après" dès la fenêtre de MEP elle-même.

Pourquoi le GTFS et pas Navitia route_schedules : l'API Navitia PRIM a renvoyé un 429
"API rate limit exceeded" persistant le 26/11/2026 (testé avec backoff, l'API SIRI
temps réel n'est elle pas affectée). Le GTFS est un fichier statique (pas de quota) :
    https://eu.ftp.opendatasoft.com/stif/GTFS/IDFM-gtfs.zip  (~140 Mo)
Téléchargé dans 13_Dashboards_IA/Referentiels/data/gtfs/<date>/IDFM-gtfs.zip (convention
déjà utilisée par Referentiels/gtfs_offre.py, qui lui calcule autre chose : les dates de
première/dernière circulation, pas les horaires d'une journée précise).

stop_times.txt pèse ~900 Mo décompressé : on ne le lit qu'UNE SEULE FOIS en streaming,
après avoir filtré trips.txt sur les route_id et service_id ciblés (pour limiter les
trip_id recherchés).

Usage :
    py -I qualitatif_echantillons.py [--date 20261201] [--heure 22:00] [--gtfs chemin.zip]
Sortie : echantillons.json
"""
import argparse
import csv
import io
import json
import pathlib
import sys
import zipfile
from datetime import datetime, timedelta

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
RELEVES = HERE / "releves"
GTFS_DIR = ROOT / "13_Dashboards_IA" / "Referentiels" / "data" / "gtfs"

sys.path.insert(0, str(ROOT / "13_Dashboards_IA" / "commun"))
import referentiel_lignes as ref_lignes  # noqa: E402

sys.path.insert(0, str(HERE))
import verifier_criteres_rollback as sup  # réutilise analyse/regrouper/dernier_releve


def dernier_gtfs():
    dossiers = sorted(GTFS_DIR.glob("*/IDFM-gtfs.zip"))
    if not dossiers:
        raise SystemExit(f"Aucun GTFS trouvé sous {GTFS_DIR} — le télécharger d'abord "
                          "(https://eu.ftp.opendatasoft.com/stif/GTFS/IDFM-gtfs.zip).")
    return dossiers[-1]


def lignes_par_reseau():
    """{réseau: ligne la plus fréquentée (code C0xxxx)} d'après le dernier relevé quantitatif."""
    releve = sup.dernier_releve()
    if releve is None:
        raise SystemExit("Aucun relevé quantitatif : lancer verifier_criteres_rollback.py d'abord.")
    choix = {}
    for clef, R in releve["reseaux"].items():
        if not R["lignes"]:
            continue
        code, l = max(R["lignes"].items(), key=lambda kv: kv[1]["courses"])
        choix[clef] = {"code": code, "num": l["num"], "mode": l["mode"], "courses": l["courses"]}
    return choix


def services_actifs(z, date_str):
    """{service_id actifs ce jour-là}, d'après calendar.txt (jour de semaine + plage de dates)
    et calendar_dates.txt (exceptions ajout/retrait, type 1/2)."""
    d = datetime.strptime(date_str, "%Y%m%d")
    jour = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"][d.weekday()]
    actifs = set()
    with z.open("calendar.txt") as f:
        for row in csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig")):
            if row["start_date"] <= date_str <= row["end_date"] and row.get(jour) == "1":
                actifs.add(row["service_id"])
    with z.open("calendar_dates.txt") as f:
        for row in csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig")):
            if row["date"] != date_str:
                continue
            if row["exception_type"] == "1":
                actifs.add(row["service_id"])
            elif row["exception_type"] == "2":
                actifs.discard(row["service_id"])
    return actifs


def trips_cibles(z, route_ids, services):
    """{trip_id: route_id} pour les lignes et services ciblés."""
    out = {}
    with z.open("trips.txt") as f:
        for row in csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig")):
            if row["route_id"] in route_ids and row["service_id"] in services:
                out[row["trip_id"]] = row["route_id"]
    return out


def noms_arrets(z, stop_ids):
    """{stop_id: (nom, parent_station)}."""
    out = {}
    with z.open("stops.txt") as f:
        for row in csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig")):
            if row["stop_id"] in stop_ids:
                out[row["stop_id"]] = (row["stop_name"], row.get("parent_station") or row["stop_id"])
    return out


def passages_proches(z, trip_route, heure_cible, fenetre_min=90):
    """Un seul passage en streaming sur stop_times.txt (~900 Mo) : pour chaque route_id cible,
    garde le passage (stop_id, heure, trip_id) le plus proche de `heure_cible`, dans une
    fenêtre de +/- fenetre_min minutes (les horaires GTFS peuvent dépasser 24:00:00 pour les
    services de nuit : on les laisse tels quels, hors-cible de toute façon ici)."""
    h, m = map(int, heure_cible.split(":"))
    cible = h * 3600 + m * 60
    meilleur = {}  # route_id -> (ecart_secondes, stop_id, trip_id, heure_str)
    with z.open("stop_times.txt") as f:
        for row in csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig")):
            route_id = trip_route.get(row["trip_id"])
            if route_id is None:
                continue
            t = row.get("departure_time") or row.get("arrival_time")
            if not t:
                continue
            try:
                hh, mm, ss = (int(x) for x in t.split(":"))
            except ValueError:
                continue
            secs = hh * 3600 + mm * 60 + ss
            ecart = abs(secs - cible)
            if ecart > fenetre_min * 60:
                continue
            prev = meilleur.get(route_id)
            if prev is None or ecart < prev[0]:
                meilleur[route_id] = (ecart, row["stop_id"], row["trip_id"], t)
    return meilleur


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="20261201")
    ap.add_argument("--heure", default="22:00")
    ap.add_argument("--gtfs")
    a = ap.parse_args()

    gtfs_path = pathlib.Path(a.gtfs) if a.gtfs else dernier_gtfs()
    print("GTFS :", gtfs_path)

    reseaux = lignes_par_reseau()
    referentiel = ref_lignes.load()
    route_ids = {f"IDFM:{v['code']}" for v in reseaux.values()}
    print(f"{len(reseaux)} réseaux, {len(route_ids)} lignes cibles.")

    z = zipfile.ZipFile(gtfs_path)
    services = services_actifs(z, a.date)
    print(f"{len(services)} services actifs le {a.date}.")
    trip_route = trips_cibles(z, route_ids, services)
    print(f"{len(trip_route)} courses (trips) ciblées.")
    if not trip_route:
        raise SystemExit("Aucune course trouvée pour ces lignes à cette date : vérifier route_id/service_id.")

    print("Lecture de stop_times.txt (~900 Mo, une seule passe)…")
    meilleur = passages_proches(z, trip_route, a.heure)
    print(f"{len(meilleur)} lignes avec un passage trouvé près de {a.heure}.")

    stop_ids = {v[1] for v in meilleur.values()}
    noms = noms_arrets(z, stop_ids)

    echantillons = {}
    for clef, v in reseaux.items():
        route_id = f"IDFM:{v['code']}"
        m = meilleur.get(route_id)
        if m is None:
            echantillons[clef] = {**v, "trouve": False}
            continue
        ecart, stop_id, trip_id, heure = m
        nom, parent = noms.get(stop_id, (stop_id, stop_id))
        # Format confirmé en direct le 26/11/2026 : STIF:StopArea:SP:<numéro>: fonctionne sur
        # stop-monitoring (teste sur 3 variantes, celle-ci donne le plus de visites -> agrège
        # mieux les quais/voies que le stop_id GTFS brut).
        monitoring_ref = f"STIF:StopArea:SP:{parent.split(':')[-1]}:"
        echantillons[clef] = {
            **v, "trouve": True, "stop_id": stop_id, "stop_nom": nom, "stop_parent": parent,
            "monitoring_ref": monitoring_ref,
            "heure_theorique": heure, "ecart_min": round(ecart / 60, 1), "trip_id": trip_id,
        }

    out = HERE / "echantillons.json"
    out.write_text(json.dumps({"date": a.date, "heure_cible": a.heure, "reseaux": echantillons},
                               ensure_ascii=False, indent=1), encoding="utf-8")
    trouves = sum(1 for e in echantillons.values() if e["trouve"])
    print(f"écrit : {out} | {trouves}/{len(echantillons)} réseaux avec un échantillon")


if __name__ == "__main__":
    main()
