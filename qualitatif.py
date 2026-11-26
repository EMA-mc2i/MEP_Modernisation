# -*- coding: utf-8 -*-
"""Moteur de l'onglet Qualitatif : structure et valeurs des balises (requêtes OD et SM arrêt),
avant (Legacy) vs après (NG), par réseau échantillonné (voir qualitatif_echantillons.py et
qualitatif_capture.py). Importé par verifier_criteres_rollback.py (fonction html_qualitatif()).
"""
import html
import json
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
CAPTURES = HERE / "captures"


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


def type_court(v):
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, (int, float)):
        return "nombre"
    return "texte"


def bloc_requete(label, avant, apres):
    if avant is None:
        return f'<p class="vide-req">{esc(label)} : pas de capture avant.</p>'
    if apres is None:
        lignes = "".join(f'<tr><td><code>{esc(k)}</code></td><td>{esc(v)}</td></tr>'
                          for k, v in sorted(avant.items())[:40])
        suite = f'<p style="font-size:11.5px;color:var(--text-muted);">… {len(avant)-40} de plus</p>' if len(avant) > 40 else ""
        return (f'<details class="detail-toggle"><summary>{esc(label)} — {len(avant)} balises (avant seulement, '
                f'{len(avant)-40 if len(avant)>40 else 0} non affichées)</summary>'
                f'<table class="niv" style="font-size:11.5px;"><tbody>{lignes}</tbody></table>{suite}</details>')
    seulement_avant = sorted(set(avant) - set(apres))
    seulement_apres = sorted(set(apres) - set(avant))
    communs = sorted(set(avant) & set(apres))
    changements = [(k, avant[k], apres[k]) for k in communs if type_court(avant[k]) != type_court(apres[k])]
    parties = []
    if seulement_avant:
        parties.append('<p><b>Disparues en NG :</b> ' + ", ".join(f"<code>{esc(k)}</code>" for k in seulement_avant) + "</p>")
    if seulement_apres:
        parties.append('<p><b>Apparues en NG :</b> ' + ", ".join(f"<code>{esc(k)}</code>" for k in seulement_apres) + "</p>")
    if changements:
        lignes = "".join(f'<tr><td><code>{esc(k)}</code></td><td>{esc(a)} <small>({type_court(a)})</small></td>'
                          f'<td>{esc(b)} <small>({type_court(b)})</small></td></tr>' for k, a, b in changements)
        parties.append(f'<table class="niv" style="font-size:11.5px;"><thead><tr><th>Balise</th><th>Avant</th><th>Après</th></tr></thead>'
                        f'<tbody>{lignes}</tbody></table>')
    if not parties:
        parties.append('<p class="vide-req">Même structure, mêmes types de valeur sur les balises communes.</p>')
    return f'<div class="qualite-bloc"><h4>{esc(label)} ({len(communs)} balises communes)</h4>{"".join(parties)}</div>'


def build_html_qualitatif():
    ech_path = HERE / "echantillons.json"
    if not ech_path.exists():
        return ('<div class="a-faire"><b>Pas encore d\'échantillons.</b> Lancer '
                '<code>qualitatif_echantillons.py</code> puis <code>qualitatif_capture.py avant</code>.</div>')
    echantillons = json.loads(ech_path.read_text(encoding="utf-8"))
    apres_existe = (CAPTURES / "apres").exists()

    intro = (f'<p>Échantillons du {esc(echantillons["date"])}, cible {esc(echantillons["heure_cible"])} '
              '(sélectionnés via le GTFS open data pour être desservis à ce moment-là — voir '
              '<code>qualitatif_echantillons.py</code>).</p>')
    if not apres_existe:
        intro += ('<div class="note"><b>Capture « après » pas encore faite.</b> Relancer '
                   '<code>qualitatif_capture.py apres</code> une fois la MEP passée pour activer la comparaison. '
                   'En dessous : la structure des balises capturées « avant » (Legacy), pour référence.</div>')

    blocs = []
    non_trouves = [k for k, e in echantillons["reseaux"].items() if not e.get("trouve")]
    for clef, e in sorted(echantillons["reseaux"].items(), key=lambda kv: kv[0]):
        if not e.get("trouve"):
            continue
        base = nom_fichier(clef)
        od_avant = charger("avant", base, "OD")
        sm_avant = charger("avant", base, "SM")
        od_apres = charger("apres", base, "OD") if apres_existe else None
        sm_apres = charger("apres", base, "SM") if apres_existe else None
        entete = (f'{esc(clef)} — {esc(e["num"])} ({esc(e["mode"])}) à {esc(e["stop_nom"])}, '
                  f'théorique {esc(e["heure_theorique"])}')
        blocs.append(
            f'<details class="niv-detail"><summary class="niv-ligne" style="grid-template-columns:1fr;">'
            f'<span class="nom">{entete}</span></summary>'
            f'<div class="corps">{bloc_requete("Requête OD (LineRef)", od_avant, od_apres)}'
            f'{bloc_requete("Requête SM (arrêt)", sm_avant, sm_apres)}</div></details>'
        )

    manques = (f'<p style="font-size:12.5px;color:var(--text-muted);">Sans échantillon (pas de passage théorique '
               f'trouvé près de l\'heure cible) : {", ".join(esc(k) for k in non_trouves)}</p>') if non_trouves else ""

    return intro + manques + f'<div class="niv-liste" style="margin-top:10px;">{"".join(blocs)}</div>'
