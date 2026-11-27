# -*- coding: utf-8 -*-
"""Moteur de l'onglet Qualitatif : structure et valeurs des balises (requêtes OD et SM arrêt),
avant (Legacy) vs après (NG), par réseau échantillonné (voir qualitatif_echantillons.py et
qualitatif_capture.py). Importé par verifier_criteres_rollback.py (fonction html_qualitatif()).

Présentation en tuiles groupées par partenaire (inspirée de la slide 10 "Producteurs" du
Go/No-Go), pas en longue liste : demandé par Erwan le 26/11/2026.
"""
import html
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
CAPTURES = HERE / "captures"

sys.path.insert(0, str(HERE))
import verifier_criteres_rollback as sup  # noqa: E402


def esc(s):
    return html.escape(str(s), quote=False)


CAP_EXEMPLES = 6  # échantillons de valeurs gardés par balise (voir plus bas pourquoi plus d'un seul)


def aplatir(obj, chemin, out):
    """{chemin de balise relatif à une course/un passage : jusqu'à CAP_EXEMPLES valeurs non
    vides distinctes VUES QUELQUE PART dans la réponse (pas indexé par occurrence : une requête
    OD/SM porte plusieurs courses/passages). Plusieurs exemples, pas un seul : une requête OD/SM
    capturée à deux instants différents ne porte pas les mêmes courses (directions, destinations,
    identifiants changent d'une capture à l'autre sans rapport avec Legacy/NG) — comparer des
    ENSEMBLES d'exemples et chercher un recouvrement est bien plus fiable qu'un exemple unique
    (découvert en simulant une comparaison le 26/11/2026 : tout ressortait "écart" sinon)."""
    if isinstance(obj, dict):
        if set(obj) <= {"value"}:
            v = obj.get("value")
            if v not in (None, ""):
                s = out.setdefault(chemin, set())
                if len(s) < CAP_EXEMPLES:
                    s.add(v)
            return
        for k, v in obj.items():
            aplatir(v, f"{chemin}.{k}" if chemin else k, out)
    elif isinstance(obj, list):
        for item in obj:
            aplatir(item, chemin, out)
    else:
        if obj not in (None, ""):
            s = out.setdefault(chemin, set())
            if len(s) < CAP_EXEMPLES:
                s.add(obj)


def charger(phase, base, suffixe):
    f = CAPTURES / phase / f"{base}__{suffixe}.json"
    if not f.exists():
        return None
    j = json.loads(f.read_text(encoding="utf-8"))
    if "_erreur" in j:
        return None
    out = {}
    aplatir(j, "", out)
    out.pop("", None)
    return out


def fmt_exemples(valeurs):
    return ", ".join(str(v) for v in sorted(valeurs, key=str)[:3]) + (" …" if len(valeurs) > 3 else "")


def nom_fichier(clef):
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in clef)


def prefixe_commun(chemins):
    """Plus long préfixe commun à une liste de chemins (coupé au dernier point entier) : les
    chemins SIRI partagent tous un long préambule (Siri.ServiceDelivery....) qui dépassait
    de la page une fois affiché sur chaque ligne (signalé par Erwan le 26/11/2026)."""
    if not chemins:
        return ""
    court = min(chemins, key=len)
    i = 0
    while i < len(court) and all(c.startswith(court[: i + 1]) for c in chemins):
        i += 1
    prefixe = court[:i]
    return prefixe[: prefixe.rfind(".") + 1] if "." in prefixe else ""


def raccourcir(d, prefixe):
    n = len(prefixe)
    return {(k[n:] or k): v for k, v in d.items()}


# Balises dont même la NOMENCLATURE peut légitimement changer d'une course à l'autre, au sein du
# même système (pas seulement la valeur) : identifiants par course où plusieurs formats
# coexistent normalement (ex. VehicleJourneyName vu en "EPAU81" ET en "97282062" dans le MÊME
# relevé Legacy, selon le service). Même la comparaison par forme ne suffit pas à les disculper :
# à exclure du diff de valeur (gardées affichées, juste pas comptées comme un écart).
BALISES_VARIABLES = {
    "ResponseTimestamp", "ResponseMessageIdentifier", "RequestMessageRef",
    "RecordedAtTime", "AimedArrivalTime", "AimedDepartureTime",
    "ExpectedArrivalTime", "ExpectedDepartureTime", "ActualArrivalTime", "ActualDepartureTime",
    "DatedVehicleJourneyRef", "ItemIdentifier", "ValidUntilTime",
    "JourneyNote", "TrainNumberRef", "VehicleJourneyName",
}


def balise_stable(chemin):
    return chemin.rsplit(".", 1)[-1] not in BALISES_VARIABLES


# Balises dont la simple PRÉSENCE (pas seulement la valeur) dépend du contexte précis d'un
# appel/passage (véhicule proche ou non, quai affecté ou non, dernier service du jour...) :
# absente d'un appel donné n'est pas absente du SYSTÈME, donc pas un signal fiable de balise
# "apparue"/"disparue en NG" — juste du hasard sur les courses échantillonnées. On les garde
# cela dit dans la comparaison de NOMENCLATURE (forme()) quand les deux côtés la portent, comme
# ExpectedQuayRef : un format différent ("Voie 2" -> "V2") reste à signaler. (Trouvé le
# 26/11/2026 : ArrivalPlatformName/ArrivalStatus/FirstOrLastJourney ressortaient en écart juste
# parce qu'absents des quelques courses échantillonnées d'un côté.)
BALISES_PRESENCE_CONDITIONNELLE = {
    "ArrivalProximityText", "DepartureProximityText",
    "ArrivalPlatformName", "DeparturePlatformName",
    "ArrivalStatus", "DepartureStatus",
    "FirstOrLastJourney",
}


def balise_presence_fiable(chemin):
    """Pour le diagnostic structurel (apparue/disparue) seulement : une balise identifiante
    (BALISES_VARIABLES) ou à présence conditionnelle (BALISES_PRESENCE_CONDITIONNELLE) n'est pas
    un signal fiable d'apparition/disparition réelle en NG."""
    nom = chemin.rsplit(".", 1)[-1]
    return nom not in BALISES_VARIABLES and nom not in BALISES_PRESENCE_CONDITIONNELLE


def a_des_courses(aplatis):
    """Faux si le relevé n'a capturé AUCUNE course/passage en circulation à cet instant (juste
    l'enveloppe SIRI : ResponseTimestamp, ProducerRef...) : dans ce cas, toutes les balises de
    détail semblent "apparues/disparues" alors qu'il n'y a simplement rien à comparer ce soir-là
    (ex. ligne qui ne circule pas à l'heure du relevé). Trouvé le 26/11/2026 sur une simulation
    à 14h58 (hors créneau MEP)."""
    return any(".EstimatedVehicleJourney." in k or k.endswith("EstimatedVehicleJourney")
               or ".MonitoredVehicleJourney." in k or k.endswith("MonitoredVehicleJourney")
               for k in aplatis)


def forme(v):
    """Signature de NOMENCLATURE, pas de structure exacte ni de casse brute : un mot unique tout
    en minuscules ("delayed") ou tout en majuscules ("DELAYED") est AMBIGU — rien n'indique s'il
    vient d'une convention SCREAMING_SNAKE ("ON_TIME") ou camelCase ("onTime"), vu qu'un enum à un
    seul mot n'a pas de 2e mot à mettre en forme. Renvoie None (signal ambigu, ignoré) sauf si la
    valeur porte un indice NON ambigu de convention : "_" + tout en majuscules -> SCREAMING_SNAKE,
    "_" + tout en minuscules -> snake_case, une majuscule APRÈS la première lettre (sans "_") ->
    camelCase. (Trouvé le 26/11/2026 : "onTime" vs "delayed" sont la MÊME convention camelCase
    côté Legacy — un simple test de casse les classait à tort comme nomenclatures différentes.)"""
    lettres = [c for c in str(v) if c.isalpha()]
    if not lettres:
        return None
    if "_" in str(v):
        if all(c.isupper() for c in lettres):
            return "SCREAMING_SNAKE"
        if all(c.islower() for c in lettres):
            return "snake_case"
        return None
    if all(c.isupper() for c in lettres) or all(c.islower() for c in lettres):
        return None  # un seul mot : ambigu, pas de 2e lettre pour révéler la convention
    return "camelCase" if lettres[0].islower() else "PascalCase"


def formes(valeurs):
    return {f for v in valeurs if (f := forme(v)) is not None}


def formes_divergent(avant_v, apres_v):
    """Vrai seulement si les DEUX côtés ont au moins un signal de nomenclature non ambigu et
    qu'ils ne se recoupent pas. Si un côté n'a que des valeurs ambiguës (formes() vide), on ne
    peut rien conclure : pas un écart."""
    fa, fb = formes(avant_v), formes(apres_v)
    return bool(fa) and bool(fb) and not (fa & fb)


def a_un_ecart(avant, apres):
    """Structure différente sur une balise au signal fiable (balise apparue/disparue, hors
    identifiants et balises à présence conditionnelle) OU, sur une balise commune STABLE, AUCUNE
    forme/nomenclature commune entre les exemples vus avant et après (ex. "rail" vs "RAIL" : même
    longueur de mot mais casse différente, à repérer ; mais pas une direction/destination/arrêt
    qui varie juste parce que la course échantillonnée n'est pas la même d'une capture à l'autre,
    du moment que la FORME reste la même — comparaison par ensemble d'exemples, voir aplatir()).
    Si un des deux côtés n'a capturé aucune course en circulation, rien à comparer (pas un écart)."""
    if not a_des_courses(avant) or not a_des_courses(apres):
        return False
    if any(balise_presence_fiable(k) for k in set(avant) ^ set(apres)):
        return True
    return any(formes_divergent(avant[k], apres[k]) for k in avant if k in apres and balise_stable(k))


def etat_echantillon(od_avant, sm_avant, od_apres, sm_apres, apres_existe):
    if not apres_existe:
        return "gris", "pré-MEP"
    if od_avant is None or sm_avant is None or od_apres is None or sm_apres is None:
        return "rouge", "capture en erreur"
    diff = a_un_ecart(od_avant, od_apres) or a_un_ecart(sm_avant, sm_apres)
    return ("orange", "écart détecté") if diff else ("vert", "stable avant/après")


def bloc_requete(label, avant, apres):
    """Renvoie (html, [noms courts des balises en écart]) : la 2e valeur sert à l'en-tête de la
    tuile, pour voir ce qui cloche SANS déplier (demande d'Erwan le 26/11/2026 : « dur
    d'identifier ce qui ne va pas quand c'est orange »)."""
    if avant is None:
        return f'<p class="vide-req">{esc(label)} : pas de capture avant.</p>', []
    prefixe = prefixe_commun(list(avant) + list(apres or []))
    note_prefixe = f'<p class="prefixe-chemin">Préfixe commun omis ci-dessous : <code>{esc(prefixe)}</code></p>' if prefixe else ""
    avant_c, apres_c = raccourcir(avant, prefixe), (raccourcir(apres, prefixe) if apres is not None else None)
    if apres_c is None:
        lignes = "".join(f'<tr><td><code>{esc(k)}</code></td><td>{esc(fmt_exemples(v))}</td></tr>'
                          for k, v in sorted(avant_c.items())[:40])
        suite = f'<p style="font-size:11.5px;color:var(--text-muted);">… {len(avant_c)-40} de plus</p>' if len(avant_c) > 40 else ""
        html_out = (f'<details class="detail-toggle"><summary>{esc(label)} — {len(avant_c)} balises (avant seulement, '
                    f'{len(avant_c)-40 if len(avant_c)>40 else 0} non affichées)</summary>{note_prefixe}'
                    f'<div class="table-scroll"><table class="niv" style="font-size:11.5px;"><tbody>{lignes}</tbody></table></div>{suite}</details>')
        return html_out, []
    if not a_des_courses(avant_c) or not a_des_courses(apres_c):
        cote = "avant (Legacy)" if not a_des_courses(avant_c) else "après (NG)"
        parties = [note_prefixe] if note_prefixe else []
        parties.append(f'<p class="vide-req">Aucune course/passage en circulation sur le relevé {esc(cote)} '
                        f'à cet instant : comparaison impossible (rien à voir avec une différence Legacy/NG).</p>')
        return f'<div class="qualite-bloc"><h4>{esc(label)}</h4>{"".join(parties)}</div>', []
    seulement_avant = sorted(k for k in (set(avant_c) - set(apres_c)) if balise_presence_fiable(k))
    seulement_apres = sorted(k for k in (set(apres_c) - set(avant_c)) if balise_presence_fiable(k))
    communs = set(avant_c) & set(apres_c)
    # Toutes les balises communes, avant ET après, comparées par FORME (nomenclature), pas par
    # valeur exacte : "Différent" = aucune forme commune entre les deux ensembles d'exemples,
    # pour une balise stable (pas un horodatage/identifiant de course). Voir forme() plus haut.
    diff_val = sorted(k for k in communs if balise_stable(k) and formes_divergent(avant_c[k], apres_c[k]))
    pareil_val = sorted(communs - set(diff_val))
    noms_ecart = [k.rsplit(".", 1)[-1] for k in (seulement_avant + seulement_apres + diff_val)]
    parties = [note_prefixe] if note_prefixe else []
    if seulement_avant:
        parties.append('<p><b>Disparues en NG :</b> ' + ", ".join(f"<code>{esc(k)}</code>" for k in seulement_avant) + "</p>")
    if seulement_apres:
        parties.append('<p><b>Apparues en NG :</b> ' + ", ".join(f"<code>{esc(k)}</code>" for k in seulement_apres) + "</p>")

    def ligne(k):
        a, b = fmt_exemples(avant_c[k]), fmt_exemples(apres_c[k])
        return f'<tr><td><code>{esc(k)}</code></td><td>{esc(a)}</td><td>{esc(b)}</td></tr>'

    if diff_val:
        corps = "".join(ligne(k) for k in diff_val)
        parties.append(f'<p class="diff-titre">⚠ {len(diff_val)} balise(s) différente(s) :</p>'
                        f'<div class="table-scroll"><table class="niv diff-table" style="font-size:11.5px;">'
                        f'<thead><tr><th>Balise</th><th>Avant (Legacy)</th><th>Après (NG)</th></tr></thead>'
                        f'<tbody>{corps}</tbody></table></div>')
    if pareil_val:
        corps = "".join(ligne(k) for k in pareil_val)
        parties.append(f'<details class="detail-toggle"><summary>{len(pareil_val)} balise(s) identique(s)</summary>'
                        f'<div class="table-scroll"><table class="niv" style="font-size:11.5px;">'
                        f'<thead><tr><th>Balise</th><th>Avant (Legacy)</th><th>Après (NG)</th></tr></thead>'
                        f'<tbody>{corps}</tbody></table></div></details>')
    if not (diff_val or pareil_val):
        parties.append('<p class="vide-req">Aucune balise commune entre avant et après.</p>')
    return f'<div class="qualite-bloc"><h4>{esc(label)}</h4>{"".join(parties)}</div>', noms_ecart


def tuile(clef, e, apres_existe):
    base = nom_fichier(clef)
    od_avant = charger("avant", base, "OD")
    sm_avant = charger("avant", base, "SM")
    od_apres = charger("apres", base, "OD") if apres_existe else None
    sm_apres = charger("apres", base, "SM") if apres_existe else None
    cls, statut = etat_echantillon(od_avant, sm_avant, od_apres, sm_apres, apres_existe)
    ligne_nom = sup.nom_ligne({"mode": e["mode"], "num": e["num"], "reseau": e.get("reseau_mode", "")})
    html_od, noms_od = bloc_requete("Requête OD (LineRef)", od_avant, od_apres)
    html_sm, noms_sm = bloc_requete("Requête SM (arrêt)", sm_avant, sm_apres)
    # Noms courts des balises en écart, remontés dans l'en-tête de la tuile : on voit ce qui
    # cloche SANS déplier (demande d'Erwan le 26/11/2026).
    noms_ecart = sorted(set(noms_od) | set(noms_sm))
    ligne_statut = (f'<span class="ecarts">{esc(", ".join(noms_ecart[:5]))}'
                    f'{" …" if len(noms_ecart) > 5 else ""}</span>' if noms_ecart else "")
    html_tuile = (
        f'<details class="tuile {cls}" id="qual-{esc(nom_fichier(clef))}"><summary>'
        f'<span class="reseau-nom">{esc(clef)}</span>'
        f'<span class="ligne-nom">{esc(ligne_nom)}</span>'
        f'<span class="arret-nom">{esc(e["stop_nom"])}</span>'
        f'<span class="heure">Théorique {esc(e["heure_theorique"])} · {esc(statut)}</span>'
        f'{ligne_statut}'
        f'</summary><div class="corps">{html_od}{html_sm}</div></details>'
    )
    return html_tuile, cls


def build_html_qualitatif():
    ech_path = HERE / "echantillons.json"
    if not ech_path.exists():
        return ('<div class="a-faire"><b>Pas encore d\'échantillons.</b> Lancer '
                '<code>qualitatif_echantillons.py</code> puis <code>qualitatif_capture.py avant</code>.</div>')
    echantillons = json.loads(ech_path.read_text(encoding="utf-8"))
    apres_existe = (CAPTURES / "apres").exists()

    intro = (f'<p>Échantillons du {esc(echantillons["date"])}, cible {esc(echantillons["heure_cible"])} '
              '(sélectionnés via le GTFS open data pour être desservis à ce moment-là — voir '
              '<code>qualitatif_echantillons.py</code>). Regroupement par partenaire comme dans l\'onglet '
              'Quantitatif ; cliquer une tuile pour voir le détail des balises OD/SM.</p>')
    if not apres_existe:
        intro += ('<div class="note"><b>Capture « après » pas encore faite.</b> Relancer '
                   '<code>qualitatif_capture.py apres</code> une fois la MEP passée pour activer la comparaison '
                   '(les tuiles passeront de gris à vert/orange). En dessous : la structure des balises '
                   'capturées « avant » (Legacy), pour référence.</div>')

    par_partenaire = {}
    non_trouves = []
    for clef, e in echantillons["reseaux"].items():
        if not e.get("trouve"):
            non_trouves.append(clef)
            continue
        par_partenaire.setdefault(e.get("partenaire", "Autre"), []).append((clef, e))

    PRIORITE = {"rouge": 3, "orange": 2, "vert": 1, "gris": 0}
    sections = []
    ecarts = []  # (clef, partenaire) en orange/rouge, remontés tout en haut
    for partenaire in sorted(par_partenaire, key=lambda p: -len(par_partenaire[p])):
        rendus = []
        cls_partenaire = "gris"
        for clef, e in sorted(par_partenaire[partenaire], key=lambda kv: kv[0]):
            html_tuile, cls = tuile(clef, e, apres_existe)
            rendus.append(html_tuile)
            if PRIORITE[cls] > PRIORITE[cls_partenaire]:
                cls_partenaire = cls
            if cls in ("orange", "rouge"):
                ecarts.append((clef, partenaire, cls))
        # Pastille sur l'en-tête du partenaire : pire statut de ses lignes (demandé le 26/11/2026,
        # pour voir d'un coup d'œil quel partenaire a un souci sans ouvrir chaque tuile).
        sections.append(f'<div class="qual-partenaire"><h3><span class="dot {cls_partenaire}"></span>{esc(partenaire)} '
                         f'<span style="font-weight:400;color:var(--text-muted);">'
                         f'({len(par_partenaire[partenaire])})</span></h3>'
                         f'<div class="qual-grille">{"".join(rendus)}</div></div>')

    bandeau_ecarts = ""
    if apres_existe:
        if ecarts:
            items = "".join(
                f'<li><a href="#qual-{esc(nom_fichier(clef))}">{esc(clef)}</a> ({esc(partenaire)})'
                f'{" — erreur de capture" if cls == "rouge" else ""}</li>'
                for clef, partenaire, cls in sorted(ecarts, key=lambda t: (t[2] != "rouge", t[0]))
            )
            bandeau_ecarts = (f'<div class="carte alerte"><h3>⚠ {len(ecarts)} réseau(x) avec un écart</h3>'
                               f'<ul style="margin:6px 0 0 18px;padding:0;">{items}</ul></div>')
        else:
            bandeau_ecarts = '<div class="carte ok"><h3>Aucun écart</h3><p>Structure et valeurs stables sur tous les réseaux échantillonnés.</p></div>'

    manques = (f'<p style="font-size:12.5px;color:var(--text-muted);">Sans échantillon (pas de passage théorique '
               f'trouvé près de l\'heure cible) : {", ".join(esc(k) for k in sorted(non_trouves))}</p>') if non_trouves else ""

    return intro + bandeau_ecarts + "".join(sections) + manques
