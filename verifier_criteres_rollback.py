# -*- coding: utf-8 -*-
"""Vérification automatique de 3 des 8 critères fonctionnels de rollback du Go/No-Go MES
(slide "Critères fonctionnels de rollback" de 15_Go_NoGo_MES\\Go_NoGo_MES_modernisation_v2.pptx),
observables via l'open data PRIM (les 5 autres critères demandent Kibana ou un accès interne au Relais) :

  1. Pas d'information sur une ligne ferrée SNCF
  2. Pas d'information sur une ligne ferrée RATP
  3. Pas d'information sur plusieurs réseaux de bus d'un même partenaire (Keolis en pratique : seul
     partenaire ayant plusieurs réseaux dans le flux PRIM)

Première validation MACRO seulement : un déclenchement ici n'est pas la décision de rollback,
il signale qu'il faut vérifier à la main (Kibana, contact partenaire) avant toute décision.

En dessous du bandeau d'alerte, le détail complet sert cette validation à la main : la même
segmentation que la slide 10 (annexe « Producteurs ») du Go/No-Go — PARTENAIRE > RÉSEAU (code
producteur PRIM) > LIGNE, avec la volumétrie (nb de courses) de chacune. Le partenaire de chaque
réseau est déduit du nom d'opérateur du référentiel open data IDFM (pas une source officielle de
regroupement producteur -> partenaire ; à valider à la main, cf. MeC_Bus_PC qui mélange plusieurs
opérateurs sous un seul réseau PRIM, comme sur la slide 10).

Repose sur la même requête globale PRIM que stats_balises_requete_globale.py (estimated-timetable,
sortie legacy, sans LineRef).

Colonnes Legacy / NG : avant la MEP, seule la colonne Legacy est renseignée (relevé courant, mis à
jour à chaque exécution) ; NG reste vide (—). Le jour de la MEP, quand Erwan dit de récupérer la
donnée, relancer UNE FOIS avec --marquer-ng : ça fige le dernier relevé connu comme référence Legacy
(releves/_legacy_fige.json) et ce relevé-ci devient le premier NG. Les exécutions suivantes (y
compris la tâche planifiée) comparent alors chaque nouveau relevé à cette référence Legacy figée :
une ligne/un réseau qui avait des courses en Legacy et n'en a plus en NG est colorée en rouge.

Usage :
    py -I verifier_criteres_rollback.py                        # interroge PRIM, compare, sauvegarde
    py -I verifier_criteres_rollback.py --marquer-ng            # à lancer UNE SEULE FOIS, le jour de la MEP
    py -I verifier_criteres_rollback.py --input fichier.json    # rejoue un flux PRIM déjà enregistré (test)
    py -I verifier_criteres_rollback.py --save-raw brut.json    # garde aussi le flux brut (hors dépôt, ~50 Mo)

Sorties : Verification_criteres_rollback.html (relevé courant), releves/<horodatage>.json (historique),
code de sortie 1 si au moins un des 3 critères est en alerte (pour un enchaînement par tâche planifiée).
"""
import argparse
import collections
import html
import json
import pathlib
import re
import sys
import time
import urllib.error
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
RELEVES = HERE / "releves"
ASSETS = ROOT / ".claude" / "skills" / "idfm-feature-sheet" / "assets"
CSS = (ASSETS / "style.css").read_text(encoding="utf-8")
LOGO_B64 = (ASSETS / "idfm_logo_cropped.b64.txt").read_text().strip()
URL = "https://prim.iledefrance-mobilites.fr/marketplace/estimated-timetable"

sys.path.insert(0, str(ROOT / "13_Dashboards_IA" / "commun"))
import cle_api  # noqa: E402
import referentiel_lignes as ref_lignes  # noqa: E402

# Modes considérés "ferré" (aligné sur l'analyse "lignes ferrées" du Go/No-Go : train/métro/tram).
MODES_FERRE = {"RAIL", "METRO", "TRAM"}

# Producteurs PRIM des 2 critères "ligne ferrée" (valeurs vues en direct le 26/11/2026).
PRODUCTEUR_SNCF = "SNCF_MAGENTA_PRD"
PRODUCTEUR_RATP = "RATP-SIV"
SEUIL_RESEAUX_PARTENAIRE = 2  # "plusieurs" = au moins 2 réseaux à 0 course simultanément

# Référence Legacy figée : écrite une seule fois par --marquer-ng (le jour de la MEP, sur demande
# d'Erwan), jamais mise à jour ensuite. Tant qu'elle n'existe pas, la colonne NG reste vide : pas de
# bascule automatique sur une date, seulement sur commande explicite ("récupère la donnée").
LEGACY_FIGE = RELEVES / "_legacy_fige.json"

# Producteur qui mélange plusieurs opérateurs sous un seul code PRIM (confirmé par Erwan le
# 26/11/2026, cf. slide 10 du Go/No-Go) : on le dissocie par opérateur (référentiel open data,
# en attendant que l'OperatorRef SIRI soit lui-même renseigné après la modernisation).
PRODUCTEURS_MIXTES = {"MeC_Bus_PC"}


def partenaire_de(operateur):
    """Regroupe un réseau sous un partenaire, à partir du nom d'opérateur du référentiel open
    data IDFM. Déduction (pas une source officielle de correspondance producteur -> partenaire),
    corrigée à la main par Erwan le 26/11/2026 : FPM -> FRANCILITÉ, RD * -> RATP CAP (régies
    départementales Bièvre/Saclay/Mantois), RATPCapIDF ("Origami / Mobicité") -> RATP CAP."""
    if operateur.startswith("Keolis"):
        return "KEOLIS"
    if operateur.startswith("Transdev"):
        return "TRANSDEV"
    if operateur.startswith("Francilité"):
        return "FRANCILITÉ"
    if operateur.startswith("RATP Cap") or operateur.startswith("RD ") or operateur == "Origami / Mobicité":
        return "RATP CAP"
    if operateur == "RATP":
        return "RATP"
    if operateur == "SNCF":
        return "SNCF"
    if operateur in ("TISSE", "STRETTO", "Grisel", "Autocars Dominique"):
        return operateur.upper()
    if operateur == "ATM Croix du Sud":
        # Partenaire à part entière, mais rattaché à MeC_Bus_PC pour ce rapport (demande d'Erwan,
        # 26/11/2026) : il n'opère que via la plateforme MeC_Bus_PC dans le flux PRIM.
        return "MeC_Bus_PC"
    return f"Autre : {operateur}" if operateur else "Autre : opérateur inconnu"


def fetch():
    req = urllib.request.Request(URL, headers={"apikey": cle_api.lire_cle(), "Accept": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        raise SystemExit(f"PRIM : HTTP {e.code}")
    return raw, time.time() - t0


def val(x):
    return x.get("value") if isinstance(x, dict) else x


def code_ligne(line_ref):
    """'STIF:Line::C01728:' -> 'C01728'."""
    parts = (line_ref or "").split(":")
    return parts[3] if len(parts) > 3 else (line_ref or "")


def analyse(j, referentiel):
    sd = j["Siri"]["ServiceDelivery"]
    ed = sd["EstimatedTimetableDelivery"][0]
    vjs = [v for f in ed.get("EstimatedJourneyVersionFrame", []) for v in f.get("EstimatedVehicleJourney", [])]

    reseaux = {}  # clef réseau -> {"lignes": {code: {"num", "mode", "operateur", "courses"}}, "courses": N}
    for v in vjs:
        dvj = val(v.get("DatedVehicleJourneyRef")) or ""
        prod = dvj.split(":")[0] if dvj else "(sans DatedVehicleJourneyRef)"
        c = code_ligne(val(v.get("LineRef")))
        mode = v.get("VehicleMode")
        mode = ((mode[0] if isinstance(mode, list) and mode else (None if isinstance(mode, list) else mode)) or "").upper()
        ref = referentiel.get(c, {})
        operateur = ref.get("operator", "") or "(hors référentiel)"
        # Producteur mixte (MeC_Bus_PC) : un réseau virtuel par opérateur, pour le dissocier
        # dans l'attente de l'OperatorRef SIRI (pas encore exploitable côté legacy, cf. remarque
        # d'Erwan du 26/11/2026).
        clef = f"{prod} · {operateur}" if prod in PRODUCTEURS_MIXTES else prod
        R = reseaux.setdefault(clef, {"lignes": {}, "courses": 0})
        R["courses"] += 1
        L = R["lignes"].setdefault(c, {"num": ref.get("num", c), "mode": mode or ref.get("mode", "").upper(),
                                        "reseau": ref.get("network", ""), "operateur": operateur, "courses": 0})
        L["courses"] += 1

    return {"timestamp": sd.get("ResponseTimestamp"), "total_courses": len(vjs), "reseaux": reseaux}


def dernier_releve():
    fichiers = sorted(f for f in RELEVES.glob("*.json") if f.name != LEGACY_FIGE.name)
    return json.loads(fichiers[-1].read_text(encoding="utf-8")) if fichiers else None


def legacy_fige():
    """La référence Legacy figée par --marquer-ng, ou None si la MEP n'a pas encore été déclarée."""
    return json.loads(LEGACY_FIGE.read_text(encoding="utf-8")) if LEGACY_FIGE.exists() else None


def ligne_ferree_disparue(avant, maintenant, producteur, label):
    if avant is None:
        return None
    lignes_avant = avant["reseaux"].get(producteur, {}).get("lignes", {})
    lignes_maintenant = maintenant["reseaux"].get(producteur, {}).get("lignes", {})
    disparues = sorted((f'{nom_ligne(l)} ({c})' for c, l in lignes_avant.items()
                        if l["courses"] > 0 and lignes_maintenant.get(c, {}).get("courses", 0) == 0))
    if not disparues:
        return None
    return {
        "critere": f"Pas d'information sur une ligne ferrée {label}",
        "detail": f"{len(disparues)} ligne(s) {label} sans course (vs relevé du {avant['timestamp']}) : " + ", ".join(disparues),
    }


def reseaux_keolis_disparus(avant, maintenant):
    if avant is None:
        return None
    partenaires_avant = regrouper(avant["reseaux"])
    partenaires_maintenant = regrouper(maintenant["reseaux"])
    reseaux_keolis = set(partenaires_avant.get("KEOLIS", {}).get("reseaux", {})) | set(partenaires_maintenant.get("KEOLIS", {}).get("reseaux", {}))
    a_zero = sorted(
        r for r in reseaux_keolis
        if partenaires_avant.get("KEOLIS", {}).get("reseaux", {}).get(r, {}).get("courses", 0) > 0
        and partenaires_maintenant.get("KEOLIS", {}).get("reseaux", {}).get(r, {}).get("courses", 0) == 0
    )
    if len(a_zero) < SEUIL_RESEAUX_PARTENAIRE:
        return None
    return {
        "critere": "Pas d'information sur plusieurs réseaux de bus du partenaire KEOLIS",
        "detail": f"{len(a_zero)} réseau(x) KEOLIS à 0 course (vs relevé du {avant['timestamp']}) : " + ", ".join(a_zero),
    }


def regrouper(reseaux):
    """{clef réseau: {...}} -> {partenaire: {"courses": N, "reseaux": {clef: {...}}}}, trié par volume."""
    partenaires = {}
    for clef, R in reseaux.items():
        # Opérateur DOMINANT du réseau (par nb de courses), pas le premier rencontré : un réseau
        # peut porter quelques lignes hors référentiel ou d'un opérateur minoritaire.
        compte = collections.Counter()
        for l in R["lignes"].values():
            compte[l["operateur"]] += l["courses"]
        operateur = compte.most_common(1)[0][0] if compte else ""
        p = partenaire_de(operateur)
        P = partenaires.setdefault(p, {"courses": 0, "reseaux": {}})
        P["courses"] += R["courses"]
        P["reseaux"][clef] = R
    return dict(sorted(partenaires.items(), key=lambda kv: -kv[1]["courses"]))


def esc(s):
    return html.escape(str(s), quote=False)


def nf(n):
    return f"{n:,}".replace(",", " ")


EXTRA_CSS = """
.sheet.wide { max-width:1180px; overflow:visible; }
.panel-body { padding:22px 36px 34px 36px; }
.stats { display:grid; grid-template-columns:repeat(auto-fit,minmax(170px,1fr)); gap:12px; margin:14px 0 22px 0; }
.stat { background:var(--bg-light); border:1px solid var(--border); border-left:4px solid var(--idf-blue); border-radius:8px; padding:12px 16px; }
.stat .n { font-size:26px; font-weight:700; color:var(--focus); line-height:1.1; }
.stat .l { font-size:11px; color:var(--text-muted); text-transform:uppercase; letter-spacing:.05em; margin-top:3px; font-weight:600; }
.carte { border-radius:8px; padding:14px 18px; margin:10px 0; border-left:5px solid; }
.carte.ok { background:var(--green-bg); border-color:var(--green); }
.carte.alerte { background:var(--red-bg); border-color:var(--red); }
.carte.premier { background:var(--bg-mid); border-color:var(--text-muted); }
.carte h3 { margin:0 0 4px 0; font-size:14px; }
.carte p { margin:0; font-size:13px; color:var(--text-muted); }
.note { background:var(--orange-bg); border-left:4px solid var(--orange); border-radius:0 8px 8px 0; padding:10px 14px; font-size:13px; line-height:1.6; margin:18px 0; }
.note b { color:#8A5A10; }
h2.sec { margin:28px 0 8px 0; font-size:16px; color:var(--anthracite); }
.dot { width:9px; height:9px; border-radius:50%; display:inline-block; margin-right:7px; flex:none; }
.dot.vert { background:var(--green); } .dot.rouge { background:var(--red); } .dot.gris { background:var(--text-muted); }

/* Une seule en-tête pour toute la section (pas une par niveau déplié, ça faisait une colonne
   répétée à chaque clic) ; les niveaux imbriqués réutilisent la même grille sans la redessiner. */
.niv-entete { display:grid; background:var(--anthracite); border-radius:8px 8px 0 0; font-size:9.5px; text-transform:uppercase; letter-spacing:.04em; }
.niv-entete > span { padding:7px 8px; color:#fff; font-weight:700; text-align:right; }
.niv-entete > span:first-child { text-align:left; }
.niv-entete > span.sep-ng { box-shadow:inset 1px 0 0 rgba(255,255,255,.25); }

.niv-liste { border:1px solid var(--border); border-top:none; border-radius:0 0 8px 8px; overflow:hidden; }
.niv-ligne { display:grid; align-items:center; padding:7px 8px; border-bottom:1px solid var(--bg-mid); font-size:12.5px; background:#fff; }
.niv-liste > .niv-ligne:last-child, .niv-liste > details.niv-detail:last-child > summary.niv-ligne { border-bottom:none; }
.niv-ligne > span.num { text-align:right; font-variant-numeric:tabular-nums; }
.niv-ligne > span.num.sep-ng { box-shadow:inset 1px 0 0 var(--bg-mid); }
.niv-ligne > span.vide { color:var(--border); text-align:right; }
.niv-ligne .nom { display:flex; align-items:center; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.niv-ligne .nom small { color:var(--text-muted); font-weight:400; margin-left:6px; }
.niv-ligne .statut { font-size:11px; }
.niv-ligne.vert .statut { color:var(--green); } .niv-ligne.rouge .statut { color:var(--red); font-weight:700; } .niv-ligne.gris .statut { color:var(--text-muted); }

details.niv-detail { background:#fff; }
details.niv-detail > summary.niv-ligne { cursor:pointer; list-style:none; }
details.niv-detail > summary.niv-ligne::-webkit-details-marker { display:none; }
details.niv-detail > summary.niv-ligne .nom::before { content:"▸ "; color:var(--focus); font-size:10px; }
details.niv-detail[open] > summary.niv-ligne .nom::before { content:"▾ "; }
details.niv-detail .corps { padding:4px 0 4px 20px; background:var(--bg-light); border-top:1px solid var(--border); }
details.niv-detail .corps .niv-liste { border:none; border-radius:0; }
details.niv-detail .corps .niv-ligne { background:transparent; }

.onglets { display:flex; gap:4px; border-bottom:2px solid var(--border); margin:18px 0 0 0; }
.onglets button { font-family:inherit; font-size:13.5px; font-weight:600; color:var(--text-muted); background:none; border:none; padding:10px 18px; cursor:pointer; border-bottom:2px solid transparent; margin-bottom:-2px; }
.onglets button.actif { color:var(--focus); border-bottom-color:var(--idf-blue); }
.onglet-corps { display:none; padding-top:18px; }
.onglet-corps.actif { display:block; }
.a-faire { background:var(--bg-light); border:1px dashed var(--border); border-radius:8px; padding:18px; color:var(--text-muted); font-size:13.5px; }
.a-faire b { color:var(--anthracite); }
.qualite-bloc { margin:6px 0 14px 0; font-size:13px; }
.qualite-bloc h4 { margin:0 0 6px 0; font-size:12.5px; color:var(--anthracite); }
.qualite-bloc code { font-size:11.5px; }
.vide-req { color:var(--text-muted); font-size:12.5px; }

.qual-partenaire { margin:20px 0; }
.qual-partenaire > h3 { font-size:12.5px; color:var(--anthracite); margin:0 0 8px 0; text-transform:uppercase; letter-spacing:.04em; }
.qual-grille { display:grid; grid-template-columns:repeat(auto-fill,minmax(190px,1fr)); gap:10px; align-items:start; }
details.tuile { border:1px solid var(--border); border-left:4px solid var(--text-muted); border-radius:8px; background:#fff; overflow:hidden; }
details.tuile[open] { grid-column:1/-1; }
details.tuile.vert { border-left-color:var(--green); } details.tuile.orange { border-left-color:var(--orange); }
details.tuile.rouge { border-left-color:var(--red); } details.tuile.gris { border-left-color:var(--text-muted); }
details.tuile > summary { list-style:none; cursor:pointer; padding:10px 12px; display:flex; flex-direction:column; gap:1px; }
details.tuile > summary::-webkit-details-marker { display:none; }
details.tuile .reseau-nom { font-size:9.5px; color:var(--text-muted); text-transform:uppercase; letter-spacing:.03em; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
details.tuile .ligne-nom { font-size:14px; font-weight:700; color:var(--anthracite); margin:2px 0 1px 0; }
details.tuile .arret-nom { font-size:11.5px; color:var(--text-muted); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
details.tuile .heure { font-size:10.5px; color:var(--text-muted); margin-top:5px; }
details.tuile .corps { padding:6px 14px 14px 14px; border-top:1px solid var(--border); background:var(--bg-light); }
"""

# Une seule grille pour les 3 niveaux (partenaire, réseau, ligne) : l'en-tête n'est dessinée
# qu'une fois, en haut de toute la section (voir entete()), pas à chaque niveau déplié.
GRID = "2.1fr 0.85fr 0.85fr 0.85fr 0.85fr 1fr"


def etat(courses_now, courses_legacy, mode_mep):
    """mode_mep=False (NG pas encore marqué) : rien n'est encore comparable, tout reste gris —
    seule la volumétrie Legacy affichée varie, la colonne NG reste vide."""
    if not mode_mep:
        return "gris", "pré-MEP"
    if courses_now > 0:
        return "vert", "actif"
    if courses_legacy and courses_legacy > 0:
        return "rouge", "disparu en NG"
    return "gris", "jamais vu"


def actives(lignes):
    return sum(1 for l in lignes.values() if l["courses"] > 0)


MODE_LABEL = {"METRO": "Métro", "TRAM": "Tram", "BUS": "Bus"}


def nom_ligne(l):
    """Nom lisible d'une ligne (ex. "RER D", "Métro 14", "Tram T8"), pas juste son numéro commercial
    nu : demandé par Erwan le 26/11/2026 pour une supervision plus fine du ferré (RATP/SNCF),
    où plusieurs lignes peuvent partager un numéro proche d'un mode à l'autre."""
    if l["mode"] == "RAIL":
        prefixe = "RER" if l.get("reseau") == "RER" else "Train"
    else:
        prefixe = MODE_LABEL.get(l["mode"], l["mode"])
    return f'{prefixe} {l["num"]}'.strip()


def entete():
    """Une seule en-tête pour toute la section Partenaire > Réseau > Ligne : les niveaux imbriqués
    réutilisent la même grille (GRID) sans la redessiner, pour ne pas dupliquer les colonnes
    à chaque dépliage."""
    lbl = ("<span>Partenaire / Réseau / Ligne</span>"
           "<span>Courses (Legacy)</span><span>Lignes (Legacy)</span>"
           '<span class="sep-ng">Courses (NG)</span><span>Lignes (NG)</span>'
           "<span>Statut</span>")
    return f'<div class="niv-entete" style="grid-template-columns:{GRID}">{lbl}</div>'


def num_span(n, dispo, sep=False):
    cls = "num" + (" sep-ng" if sep else "")
    if n is None or not dispo:
        return f'<span class="{cls} vide">—</span>'
    return f'<span class="{cls}">{nf(n)}</span>'


def data_row(id_html, legacy_vals, ng_vals, mode_mep, cls, statut_txt, tag):
    nums = (num_span(legacy_vals[0], True) + num_span(legacy_vals[1], True)
            + num_span(ng_vals[0], mode_mep, sep=True) + num_span(ng_vals[1], mode_mep))
    return (f'<{tag} class="niv-ligne {cls}" style="grid-template-columns:{GRID}">'
            f'{id_html}{nums}<span class="statut">{statut_txt}</span></{tag}>')


def html_lignes(lignes, lignes_legacy, mode_mep):
    rows = []
    for c, l in sorted(lignes.items(), key=lambda kv: -kv[1]["courses"]):
        legacy_n = lignes_legacy.get(c, {}).get("courses", 0)
        cls, txt = etat(l["courses"], legacy_n, mode_mep)
        id_html = f'<span class="nom">{esc(nom_ligne(l))} <small>{esc(c)}</small></span>'
        rows.append(data_row(id_html, [legacy_n, None], [l["courses"], None], mode_mep, cls, txt, "div"))
    return f'<div class="niv-liste">{"".join(rows)}</div>'


def html_reseaux(reseaux, reseaux_legacy, mode_mep):
    blocs = []
    for clef, R in sorted(reseaux.items(), key=lambda kv: -kv[1]["courses"]):
        Rl = reseaux_legacy.get(clef, {"courses": 0, "lignes": {}})
        cls, txt = etat(R["courses"], Rl["courses"], mode_mep)
        id_html = f'<span class="nom"><span class="dot {cls}"></span>{esc(clef)}</span>'
        summary = data_row(id_html, [Rl["courses"], actives(Rl["lignes"])],
                            [R["courses"], actives(R["lignes"])], mode_mep, cls, txt, "summary")
        blocs.append(f'<details class="niv-detail">{summary}<div class="corps">'
                      f'{html_lignes(R["lignes"], Rl["lignes"], mode_mep)}</div></details>')
    return f'<div class="niv-liste">{"".join(blocs)}</div>'


def html_partenaires(partenaires_maintenant, partenaires_legacy, mode_mep):
    blocs = []
    for p, P in partenaires_maintenant.items():
        Pl = partenaires_legacy.get(p, {"courses": 0, "reseaux": {}})
        cls, txt = etat(P["courses"], Pl["courses"], mode_mep)
        lignes_legacy_p = sum(actives(R["lignes"]) for R in Pl["reseaux"].values())
        lignes_maintenant_p = sum(actives(R["lignes"]) for R in P["reseaux"].values())
        id_html = f'<span class="nom"><span class="dot {cls}"></span><b>{esc(p)}</b></span>'
        summary = data_row(id_html, [Pl["courses"], lignes_legacy_p],
                            [P["courses"], lignes_maintenant_p], mode_mep, cls, txt, "summary")
        blocs.append(f'<details class="niv-detail">{summary}<div class="corps">'
                      f'{html_reseaux(P["reseaux"], Pl["reseaux"], mode_mep)}</div></details>')
    return entete() + f'<div class="niv-liste">{"".join(blocs)}</div>'


def build_html(maintenant, legacy, alertes, mode_mep, out):
    if not mode_mep:
        bandeau = ('<div class="carte premier"><h3>NG pas encore marqué</h3>'
                   '<p>La colonne NG reste vide tant que la MEP n\'a pas été déclarée. '
                   'Le jour J, relancer avec <code>--marquer-ng</code> pour figer la référence Legacy et démarrer la comparaison.</p></div>')
    elif alertes:
        bandeau = "".join(f'<div class="carte alerte"><h3>⚠ {esc(a["critere"])}</h3><p>{esc(a["detail"])}</p></div>' for a in alertes)
    else:
        bandeau = '<div class="carte ok"><h3>Aucune alerte</h3><p>Les 3 critères surveillés sont OK par rapport à la référence Legacy.</p></div>'

    partenaires_maintenant = regrouper(maintenant["reseaux"])
    partenaires_legacy = regrouper(legacy["reseaux"])
    n_reseaux = sum(len(P["reseaux"]) for P in partenaires_maintenant.values())
    n_lignes = sum(len(R["lignes"]) for P in partenaires_maintenant.values() for R in P["reseaux"].values())

    stats = (
        '<div class="stats">'
        f'<div class="stat"><div class="n">{nf(maintenant["total_courses"])}</div><div class="l">Courses (relevé global)</div></div>'
        f'<div class="stat"><div class="n">{len(partenaires_maintenant)}</div><div class="l">Partenaires</div></div>'
        f'<div class="stat"><div class="n">{n_reseaux}</div><div class="l">Réseaux</div></div>'
        f'<div class="stat"><div class="n">{n_lignes}</div><div class="l">Lignes au relevé</div></div>'
        "</div>"
    )
    reference_txt = (f"référence Legacy figée le {esc(legacy['timestamp'])}" if mode_mep
                      else "lui-même (NG pas encore marqué, voir ci-dessous)")

    onglet_quanti = f"""
    <p>Relevé du <b>{esc(maintenant["timestamp"])}</b> (UTC), comparé au {reference_txt}.
    3 des 8 critères du Go/No-Go MES, observables via l'open data PRIM (les 5 autres demandent Kibana ou un accès interne au Relais, hors périmètre de ce script).</p>
    <div class="note"><b>Première validation macro seulement.</b> Une alerte ici signale qu'il faut vérifier à la main (Kibana, contact partenaire) avant toute décision de rollback ; ce n'est pas la décision elle-même. Le regroupement réseau -> partenaire vient de l'opérateur dominant du référentiel open data IDFM, pas d'une source officielle de correspondance producteur PRIM -> partenaire : à valider à la main.</div>
    {stats}
    {bandeau}
    <h2 class="sec">Détail par partenaire · réseau · ligne — Legacy / NG</h2>
    <p style="font-size:13px;color:var(--text-muted);margin:0 0 8px 0;">{"Rouge = avait des courses en Legacy et n'en a plus en NG. Gris = aucune course aux deux relevés." if mode_mep else "La colonne Legacy montre le relevé courant (mis à jour toutes les 2h) ; NG reste vide (—) jusqu'au déclenchement du jour de la MEP."}</p>
    {html_partenaires(partenaires_maintenant, partenaires_legacy, mode_mep)}
    """

    page = f"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Vérification des critères de rollback</title>
<style>{CSS}{EXTRA_CSS}</style></head><body>
<div class="sheet wide">
  <header class="sheet-header">
    <div class="header-tags"><span class="doc-status pending">Relevé automatique</span><span class="pi-tag">PRIM {esc(maintenant["timestamp"][:16])}</span></div>
    <!--LOGO--><div class="logo-chip"><img class="logo" src="data:image/png;base64,{LOGO_B64}" alt="Île-de-France Mobilités"></div><!--/LOGO-->
    <div class="htext"><p class="eyebrow">Relais IVTR · Supervision post-MEP</p>
    <h1>Vérification des critères fonctionnels de rollback</h1></div>
  </header>
  <div class="panel-body">
    <div class="onglets">
      <button class="actif" onclick="ongletAfficher('quanti', this)">Quantitatif</button>
      <button onclick="ongletAfficher('quali', this)">Qualitatif</button>
    </div>
    <div id="onglet-quanti" class="onglet-corps actif">{onglet_quanti}</div>
    <div id="onglet-quali" class="onglet-corps">{html_qualitatif()}</div>
  </div>
  <div class="sheet-footer"><span>Île-de-France Mobilités — Relais IVTR</span><span>Généré par verifier_criteres_rollback.py</span></div>
</div>
<script>
function ongletAfficher(nom, btn) {{
  document.querySelectorAll('.onglet-corps').forEach(function(d) {{ d.classList.remove('actif'); }});
  document.querySelectorAll('.onglets button').forEach(function(b) {{ b.classList.remove('actif'); }});
  document.getElementById('onglet-' + nom).classList.add('actif');
  btn.classList.add('actif');
}}
</script>
</body></html>
"""
    out.write_text(page, encoding="utf-8")
    # index.html = copie publiée sur GitHub Pages, sans le logo IDFM (demande d'Erwan le 26/11 :
    # un site perso ne doit pas passer pour un site officiel IDFM, même règle que les dashboards IA).
    page_publique = re.sub(r"<!--LOGO-->.*?<!--/LOGO-->", "", page, flags=re.S)
    (HERE / "index.html").write_text(page_publique, encoding="utf-8")


def html_qualitatif():
    """Onglet « Qualitatif » : comparaison de structure et de valeurs des balises (OD / SM arrêt)
    avant/après MEP, par réseau. Échantillons choisis via le GTFS (qualitatif_echantillons.py,
    pas l'API Navitia : voir le commentaire dans ce script sur le 429 rencontré le 26/11/2026),
    capturés par qualitatif_capture.py, comparés par qualitatif.py."""
    sys.path.insert(0, str(HERE))
    import qualitatif
    return qualitatif.build_html_qualitatif()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input")
    ap.add_argument("--save-raw")
    ap.add_argument("--marquer-ng", action="store_true",
                     help="Fige le dernier relevé connu comme référence Legacy, puis ce relevé-ci devient le premier NG. "
                          "À lancer une seule fois, le jour de la MEP, quand Erwan dit de récupérer la donnée.")
    a = ap.parse_args()
    if a.input:
        raw = pathlib.Path(a.input).read_bytes()
    else:
        raw, _ = fetch()
    if a.save_raw:
        pathlib.Path(a.save_raw).write_bytes(raw)

    RELEVES.mkdir(exist_ok=True)
    referentiel = ref_lignes.load()
    maintenant = analyse(json.loads(raw), referentiel)

    if a.marquer_ng:
        if LEGACY_FIGE.exists():
            raise SystemExit(f"NG déjà marqué (référence figée le {legacy_fige()['timestamp']}) : "
                              f"supprimer {LEGACY_FIGE} à la main pour re-figer.")
        reference = dernier_releve()
        if reference is None:
            raise SystemExit("Aucun relevé antérieur à figer comme référence Legacy : relancer sans --marquer-ng d'abord.")
        LEGACY_FIGE.write_text(json.dumps(reference, ensure_ascii=False, indent=1), encoding="utf-8")
        print("référence Legacy figée :", reference["timestamp"])

    legacy = legacy_fige()
    mode_mep = legacy is not None
    if not mode_mep:
        legacy = maintenant  # pas encore de MEP déclarée : Legacy = le relevé courant lui-même

    alertes = []
    if mode_mep:
        for prod, label in ((PRODUCTEUR_SNCF, "SNCF"), (PRODUCTEUR_RATP, "RATP")):
            a_ = ligne_ferree_disparue(legacy, maintenant, prod, label)
            if a_:
                alertes.append(a_)
        a_ = reseaux_keolis_disparus(legacy, maintenant)
        if a_:
            alertes.append(a_)

    ts = maintenant["timestamp"].replace(":", "-")
    (RELEVES / f"{ts}.json").write_text(json.dumps(maintenant, ensure_ascii=False, indent=1), encoding="utf-8")

    out = HERE / "Verification_criteres_rollback.html"
    build_html(maintenant, legacy, alertes, mode_mep, out)

    print("ecrit :", out, "|", maintenant["total_courses"], "courses |", len(alertes), "alerte(s)", "| mode_mep=", mode_mep)
    for a_ in alertes:
        print(" -", a_["critere"], ":", a_["detail"])
    sys.exit(1 if alertes else 0)


if __name__ == "__main__":
    main()
