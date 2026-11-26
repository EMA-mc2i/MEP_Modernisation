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


def aplatir(obj, chemin, out):
    """{chemin de balise relatif à une course/un passage : un exemple de valeur non vide}.
    Les listes ne sont PAS indexées : une requête OD/SM contient plusieurs courses/passages,
    on veut la liste des balises qui existent QUELQUE PART, pas une par occurrence."""
    if isinstance(obj, dict):
        if set(obj) <= {"value"}:
            v = obj.get("value")
            if v not in (None, "") and chemin not in out:
                out[chemin] = v
            return
        for k, v in obj.items():
            aplatir(v, f"{chemin}.{k}" if chemin else k, out)
    elif isinstance(obj, list):
        for item in obj:
            aplatir(item, chemin, out)
    else:
        if obj not in (None, "") and chemin not in out:
            out[chemin] = obj


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


def a_un_ecart(avant, apres):
    """Structure différente (balise apparue/disparue) OU valeur différente sur une balise
    commune (ex. "rail" vs "RAIL" : même type, format différent, à repérer aussi)."""
    if set(avant) ^ set(apres):
        return True
    return any(str(avant[k]) != str(apres[k]) for k in avant if k in apres)


def etat_echantillon(od_avant, sm_avant, od_apres, sm_apres, apres_existe):
    if not apres_existe:
        return "gris", "pré-MEP"
    if od_avant is None or sm_avant is None or od_apres is None or sm_apres is None:
        return "rouge", "capture en erreur"
    diff = a_un_ecart(od_avant, od_apres) or a_un_ecart(sm_avant, sm_apres)
    return ("orange", "écart détecté") if diff else ("vert", "stable avant/après")


def bloc_requete(label, avant, apres):
    if avant is None:
        return f'<p class="vide-req">{esc(label)} : pas de capture avant.</p>'
    prefixe = prefixe_commun(list(avant) + list(apres or []))
    note_prefixe = f'<p class="prefixe-chemin">Préfixe commun omis ci-dessous : <code>{esc(prefixe)}</code></p>' if prefixe else ""
    avant_c, apres_c = raccourcir(avant, prefixe), (raccourcir(apres, prefixe) if apres is not None else None)
    if apres_c is None:
        lignes = "".join(f'<tr><td><code>{esc(k)}</code></td><td>{esc(v)}</td></tr>'
                          for k, v in sorted(avant_c.items())[:40])
        suite = f'<p style="font-size:11.5px;color:var(--text-muted);">… {len(avant_c)-40} de plus</p>' if len(avant_c) > 40 else ""
        return (f'<details class="detail-toggle"><summary>{esc(label)} — {len(avant_c)} balises (avant seulement, '
                f'{len(avant_c)-40 if len(avant_c)>40 else 0} non affichées)</summary>{note_prefixe}'
                f'<div class="table-scroll"><table class="niv" style="font-size:11.5px;"><tbody>{lignes}</tbody></table></div>{suite}</details>')
    seulement_avant = sorted(set(avant_c) - set(apres_c))
    seulement_apres = sorted(set(apres_c) - set(avant_c))
    communs = set(avant_c) & set(apres_c)
    # Toutes les balises communes, avant ET après, valeur contre valeur : pas seulement celles
    # dont le TYPE a changé (ex. "rail" vs "RAIL" ont le même type mais pas le même format —
    # demande d'Erwan le 26/11/2026, pour pouvoir juger les écarts de format à l'œil).
    diff_val = sorted((k for k in communs if str(avant_c[k]) != str(apres_c[k])))
    pareil_val = sorted(communs - set(diff_val))
    parties = [note_prefixe] if note_prefixe else []
    if seulement_avant:
        parties.append('<p><b>Disparues en NG :</b> ' + ", ".join(f"<code>{esc(k)}</code>" for k in seulement_avant) + "</p>")
    if seulement_apres:
        parties.append('<p><b>Apparues en NG :</b> ' + ", ".join(f"<code>{esc(k)}</code>" for k in seulement_apres) + "</p>")

    def ligne(k, cls=""):
        a, b = avant_c[k], apres_c[k]
        return f'<tr class="{cls}"><td><code>{esc(k)}</code></td><td>{esc(a)}</td><td>{esc(b)}</td></tr>'

    if diff_val or pareil_val:
        corps = "".join(ligne(k, "diff") for k in diff_val) + "".join(ligne(k) for k in pareil_val)
        parties.append(f'<p style="font-size:11.5px;color:var(--text-muted);">{len(diff_val)} valeur(s) différente(s) '
                        f'sur {len(communs)} balises communes (en orange ci-dessous, triées en premier).</p>'
                        f'<div class="table-scroll"><table class="niv" style="font-size:11.5px;">'
                        f'<thead><tr><th>Balise</th><th>Avant (Legacy)</th><th>Après (NG)</th></tr></thead>'
                        f'<tbody>{corps}</tbody></table></div>')
    else:
        parties.append('<p class="vide-req">Aucune balise commune entre avant et après.</p>')
    return f'<div class="qualite-bloc"><h4>{esc(label)}</h4>{"".join(parties)}</div>'


def tuile(clef, e, apres_existe):
    base = nom_fichier(clef)
    od_avant = charger("avant", base, "OD")
    sm_avant = charger("avant", base, "SM")
    od_apres = charger("apres", base, "OD") if apres_existe else None
    sm_apres = charger("apres", base, "SM") if apres_existe else None
    cls, statut = etat_echantillon(od_avant, sm_avant, od_apres, sm_apres, apres_existe)
    ligne_nom = sup.nom_ligne({"mode": e["mode"], "num": e["num"], "reseau": e.get("reseau_mode", "")})
    html_tuile = (
        f'<details class="tuile {cls}" id="qual-{esc(nom_fichier(clef))}"><summary>'
        f'<span class="reseau-nom">{esc(clef)}</span>'
        f'<span class="ligne-nom">{esc(ligne_nom)}</span>'
        f'<span class="arret-nom">{esc(e["stop_nom"])}</span>'
        f'<span class="heure">Théorique {esc(e["heure_theorique"])} · {esc(statut)}</span>'
        f'</summary><div class="corps">'
        f'{bloc_requete("Requête OD (LineRef)", od_avant, od_apres)}'
        f'{bloc_requete("Requête SM (arrêt)", sm_avant, sm_apres)}'
        f'</div></details>'
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

    sections = []
    ecarts = []  # (clef, partenaire) en orange/rouge, remontés tout en haut
    for partenaire in sorted(par_partenaire, key=lambda p: -len(par_partenaire[p])):
        rendus = []
        for clef, e in sorted(par_partenaire[partenaire], key=lambda kv: kv[0]):
            html_tuile, cls = tuile(clef, e, apres_existe)
            rendus.append(html_tuile)
            if cls in ("orange", "rouge"):
                ecarts.append((clef, partenaire, cls))
        sections.append(f'<div class="qual-partenaire"><h3>{esc(partenaire)} '
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
