#!/usr/bin/env python
"""Allege le trace des departements pour la carte du tableau de bord.

Pourquoi. La choroplethe embarque le GeoJSON dans la figure, et Streamlit
serialise la figure entiere a chaque rerun : chaque clic renvoyait 7,12 Mio au
navigateur. Le trace portait 193 971 points pour un dessin large d'environ
800 px, ou un pixel vaut a peu pres 1,5 km : une precision au metre qu'aucun
ecran ne peut montrer.

Methode. Douglas-Peucker avec une tolerance en degres, puis arrondi des
coordonnees. La tolerance retenue pour le fichier du depot est 0.002 degre,
soit environ 223 m, un septieme de pixel.

Ce que le script verifie avant d'ecrire, et qui le fait echouer sinon : meme
nombre de features, memes codes, memes noms, memes types de geometrie, et
variation d'aire sous 1 % pour chaque departement.

Le script boucle jusqu'au point fixe. Une seule passe ne suffit pas : l'arrondi
deplace les sommets de quelques metres, ce qui rend une poignee de points
supprimables a la passe suivante. Mesure sur ce fichier : 193 971 points a
l'entree, 51 444 apres une passe, 51 097 apres deux, puis plus rien. Le
resultat ne depend donc pas du nombre de fois ou l'on relance le script.

Usage :
    python scripts/simplify_geojson.py app/departements.geojson
    python scripts/simplify_geojson.py source.geojson -o sortie.geojson -t 0.002
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

DEFAULT_TOLERANCE = 0.002
DEFAULT_DECIMALS = 4
MAX_AREA_CHANGE_PCT = 1.0


def point_segment_distance(point, start, end):
    """Distance d'un point au segment, en degres."""
    (px, py), (ax, ay), (bx, by) = point, start, end
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def douglas_peucker(points, tolerance):
    """Garde les sommets dont l'ecart au segment depasse la tolerance."""
    if len(points) < 3:
        return list(points)
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        i, j = stack.pop()
        worst, index = 0.0, -1
        for k in range(i + 1, j):
            d = point_segment_distance(points[k], points[i], points[j])
            if d > worst:
                worst, index = d, k
        if worst > tolerance and index > 0:
            keep[index] = True
            stack.append((i, index))
            stack.append((index, j))
    return [p for p, k in zip(points, keep) if k]


def simplify_ring(ring, tolerance, decimals):
    """Un anneau reste ferme et garde au moins quatre sommets."""
    simplified = douglas_peucker([tuple(p) for p in ring], tolerance)
    rounded = [[round(x, decimals), round(y, decimals)] for x, y in simplified]
    deduped = [rounded[0]]
    for p in rounded[1:]:
        if p != deduped[-1]:
            deduped.append(p)
    if deduped[0] != deduped[-1]:
        deduped.append(deduped[0])
    if len(deduped) < 4:
        return [[round(x, decimals), round(y, decimals)] for x, y in ring]
    return deduped


def simplify_coordinates(coords, tolerance, decimals):
    if isinstance(coords[0][0], (int, float)):
        return simplify_ring(coords, tolerance, decimals)
    return [simplify_coordinates(c, tolerance, decimals) for c in coords]


def ring_area(coords):
    """Somme des aires absolues des anneaux, en degres carres."""
    total = 0.0
    if isinstance(coords[0][0], (int, float)):
        s = 0.0
        for k in range(len(coords) - 1):
            s += coords[k][0] * coords[k + 1][1] - coords[k + 1][0] * coords[k][1]
        return abs(s) / 2
    for c in coords:
        total += ring_area(c)
    return total


def count_points(geojson):
    total = 0

    def walk(c):
        nonlocal total
        if isinstance(c[0], (int, float)):
            total += 1
        else:
            for x in c:
                walk(x)

    for feature in geojson["features"]:
        walk(feature["geometry"]["coordinates"])
    return total


def simplify_once(geojson, tolerance, decimals):
    out = {"type": geojson["type"], "features": []}
    for feature in geojson["features"]:
        out["features"].append({
            "type": feature["type"],
            "properties": feature["properties"],
            "geometry": {
                "type": feature["geometry"]["type"],
                "coordinates": simplify_coordinates(
                    feature["geometry"]["coordinates"], tolerance, decimals),
            },
        })
    return out


def simplify(geojson, tolerance, decimals, max_passes=5):
    """Boucle jusqu'au point fixe, sinon le resultat depend du nombre de passes."""
    result, passes = geojson, 0
    while passes < max_passes:
        nxt = simplify_once(result, tolerance, decimals)
        passes += 1
        # Garder `nxt` AVANT de sortir : sur une entree deja simplifiee la
        # premiere passe ne retire aucun point, et sortir sans l'affectation
        # rendait l'entree telle quelle, arrondi compris.
        stable = count_points(nxt) == count_points(result)
        result = nxt
        if stable:
            break
    changes = []
    for before_feature, after_feature in zip(geojson["features"], result["features"]):
        before = ring_area(before_feature["geometry"]["coordinates"])
        after = ring_area(after_feature["geometry"]["coordinates"])
        changes.append((before_feature["properties"]["code"],
                        abs(after - before) / before * 100 if before else 0.0))
    return result, changes, passes


def check(source, result, changes):
    """Rend la liste des controles echoues : vide veut dire que tout tient."""
    failures = []
    if len(source["features"]) != len(result["features"]):
        failures.append("nombre de features different")
    for key in ("code", "nom"):
        if ([f["properties"].get(key) for f in source["features"]]
                != [f["properties"].get(key) for f in result["features"]]):
            failures.append("propriete {} modifiee".format(key))
    if ([f["geometry"]["type"] for f in source["features"]]
            != [f["geometry"]["type"] for f in result["features"]]):
        failures.append("type de geometrie modifie")
    worst = max(changes, key=lambda c: c[1])
    if worst[1] > MAX_AREA_CHANGE_PCT:
        failures.append("aire du departement {} changee de {:.2f} %".format(*worst))
    return failures


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", type=Path, help="GeoJSON a alleger")
    parser.add_argument("-o", "--output", type=Path,
                        help="fichier de sortie (defaut : ecrase la source)")
    parser.add_argument("-t", "--tolerance", type=float, default=DEFAULT_TOLERANCE,
                        help="tolerance Douglas-Peucker en degres (defaut {})"
                             .format(DEFAULT_TOLERANCE))
    parser.add_argument("-d", "--decimals", type=int, default=DEFAULT_DECIMALS,
                        help="decimales conservees (defaut {})".format(DEFAULT_DECIMALS))
    args = parser.parse_args(argv)
    destination = args.output or args.source

    source = json.loads(args.source.read_text(encoding="utf-8"))
    result, changes, passes = simplify(source, args.tolerance, args.decimals)

    failures = check(source, result, changes)
    if failures:
        for f in failures:
            print("ECHEC :", f)
        return 1

    text = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    before_bytes = len(args.source.read_bytes())
    destination.write_text(text, encoding="utf-8")

    worst_code, worst_pct = max(changes, key=lambda c: c[1])
    mean_pct = sum(c[1] for c in changes) / len(changes)
    print("tolerance     : {} degre, soit environ {:.0f} m"
          .format(args.tolerance, args.tolerance * 111320))
    print("features      : {} (codes, noms et types inchanges)".format(len(result["features"])))
    print("points        : {:,} -> {:,}".format(count_points(source), count_points(result))
          .replace(",", " "))
    print("octets        : {:,} -> {:,}".format(before_bytes, len(text.encode()))
          .replace(",", " "))
    print("aire          : pire {} a {:.2f} %, moyenne {:.2f} %"
          .format(worst_code, worst_pct, mean_pct))
    print("passes        : {} (jusqu'au point fixe)".format(passes))
    print("ecrit         :", destination)
    return 0


if __name__ == "__main__":
    sys.exit(main())
