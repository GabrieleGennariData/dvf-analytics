#!/usr/bin/env python
"""Fige le referentiel de la comparaison de reproductibilite par contenu, dans
`scripts/reference_fingerprint.json`.

Un `cmp` sur les fichiers DuckDB echouerait toujours, meme sur un pipeline
correct : la comparaison se fait par contenu, et elle a besoin d'un
referentiel pris AVANT, present dans un clone a froid ou la base de travail
n'existe pas encore. Ce JSON est ce referentiel.

Ce qu'il enregistre :

- les 8 tables de `data/dvf.duckdb`, pas les 3 exportees : une regression en
  amont peut laisser les marts intacts et casser tout le reste ;
- trois sommes de controle independantes de l'ordre (`count`, `bit_xor(hash)`,
  `sum(hash::HUGEINT)`), toutes en arithmetique entiere exacte, donc
  comparables entre machines la ou les octets ne le sont pas ;
- un echantillon de cellules interrogees par cle : les sommes disent QUE
  quelque chose a change, les cellules disent combien et ou ;
- `code_ref`, les tree hashes de `dbt/` : contre quel CODE le referentiel a
  ete pris, ce qui decide si une comparaison de tables est legitime ;
- les sha256 des deux entrees brutes, seule chose comparee a l'octet ici : ce
  ne sont pas des artefacts regeneres mais la source, et si elles divergent
  c'est l'editeur qui a change, pas le projet.

Ce qu'il n'enregistre PAS, et c'est facile a rater : la taille en octets de
`data/dvf.duckdb`. Elle change a chaque rebuild a donnees inchangees, et
l'inscrire injecterait un signal de niveau octets dans un referentiel de
niveau contenu.

Garde-fou : ATTACH en READ_ONLY, et verification finale que taille et mtime
sont inchanges et qu'aucun `.wal` n'est apparu.

Usage : python scripts/reference_fingerprint.py
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import duckdb

# Chemins resolus depuis le fichier et pas depuis le cwd : ce script s'execute
# aussi dans un clone, depuis un dossier que personne ne controle.
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SRC = ROOT / "data" / "dvf.duckdb"
OUT = HERE / "reference_fingerprint.json"

# Les entrees brutes du pipeline DVF+ : l'archive nationale
# du Cerema telle que publiee (les 97 CSV en derivent par extraction, et
# hacher l'archive evite 8,3 Go de sha256 a chaque run) et le referentiel COG
# de l'Insee dont nait dim_commune. Liste explicite et pas un glob : un
# fichier en plus ou en moins doit arreter le script, pas passer inapercu.
RAW_FILES = (
    ROOT / "data" / "raw_dvfplus" / "DVF_PLUS_2026_1_CSV_R999_ED251.7z",
    ROOT / "data" / "raw_cog" / "v_commune_2026.csv",
)

# Ancrages externes. Sans eux, un referentiel construit sur une base vide ou
# tronquee s'enregistrerait comme referentiel valide et se comparerait avec
# succes a une autre base vide.
EXPECTED_TABLES = 8

# Le couple de versions valide. L'ecrivain n'est pas mesurable d'ici :
# `pragma_version()` rapporte le moteur qui lit maintenant - le pip duckdb -
# pas celui qui a ecrit le fichier. Le DuckDB embarque dans Fusion ne laisse
# pas sa version dans la base sous une forme interrogeable par le lecteur,
# donc le couple se DECLARE. Le numero de version de Fusion est hors sujet :
# il peut changer sans que le moteur change.
WRITER_DECLARED = {
    "_note": (
        "Couple writer->reader valide : le DuckDB embarque dans dbt Fusion qui ecrit "
        "data/dvf.duckdb. DECLARE et non mesure : pragma_version() rapporte le moteur "
        "qui lit, pas celui qui a ecrit. A comparer a la main avec "
        "'select library_version, source_id from pragma_version()' execute par dbt, "
        "pas par le pip duckdb."
    ),
    "library_version": "v1.5.4",
    "source_id": "08e34c447b",
}


def fail(msg):
    """Critere en echec = s'arreter, jamais forcer le pass."""
    print(f"\n[ECHEC] {msg}", file=sys.stderr)
    raise SystemExit(1)


def checksums(con, ref):
    """count + bit_xor(hash) + sum(hash::HUGEINT), les trois nombres de l'export.

    Aucun des trois n'est redondant : `count` ne voit pas une ligne changee,
    `bit_xor` est aveugle aux paires de doublons qui s'annulent entre elles,
    `sum` distingue les multiplicites. Tous trois en entier exact, donc
    independants de l'ordre de scan et comparables entre machines.
    """
    c, x, s = con.execute(
        f"select count(*), bit_xor(hash(t)), sum(hash(t)::HUGEINT) from {ref} t"
    ).fetchone()
    return {"bit_xor_hash": x, "count": c, "sum_hash": s}


def schema_of(con, ref):
    """[nom, type] dans l'ordre de declaration : l'ordre fait partie du schema."""
    return [[r[0], r[1]] for r in con.execute(f"describe {ref}").fetchall()]


def rows_as_dicts(con, sql, params=None):
    """Lignes en dictionnaires colonne -> valeur, noms pris du curseur.

    C'est ce qui garde l'echantillon de cellules *interroge* et pas *recopie* :
    si demain un mart gagne ou perd une colonne, le referentiel la prend ou la
    perd tout seul au lieu de continuer a declarer une forme qui n'existe plus.
    """
    cur = con.execute(sql, params or [])
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def sha256_of(path):
    """sha256 par blocs : l'archive nationale fait ~1 Go."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def git_out(*args):
    """`git <args>` execute depuis la racine du depot, ou s'arrete."""
    try:
        out = subprocess.run(
            ["git", *args], cwd=str(ROOT), capture_output=True, text=True, check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = (getattr(exc, "stderr", "") or "").strip() or str(exc)
        fail(f"git {' '.join(args)} inexecutable dans {ROOT} : {detail}")
    return out.stdout


def git_head():
    return git_out("rev-parse", "HEAD").strip()


def code_ref():
    """Contre QUEL CODE le referentiel a ete pris.

    `git_head` seul dit **quand**, pas **contre quoi** : c'est le commit de la
    racine, et il bouge aussi quand seul un script ou le README change. Deux
    referentiels a `git_head` different peuvent decrire le meme pipeline, et
    deux au meme `git_head` ne peuvent pas en decrire deux differents - mais
    pour le savoir il faudrait aller lire le diff. Dans un clone, c'est la
    premiere question qui se pose : **le code qui a produit le referentiel
    est-il celui que je m'apprete a executer ?** Les deux tree hashes y
    repondent sans rien ouvrir.

    - `dbt_tree` (`HEAD:dbt`) est le champ **autoritatif** : il couvre
      `models/`, `tests/`, `dbt_project.yml`, `packages.yml`,
      `package-lock.yml` et `profiles.yml`, c'est-a-dire tout ce qui decide de
      ce que `dbt build` produit. S'il coincide, la comparaison des tables est
      legitime.
    - `models_tree` (`HEAD:dbt/models`) ne sert qu'au **diagnostic**, quand
      `dbt_tree` diverge : il separe « un modele a change » - et alors une
      difference dans les tables est attendue - de « seul le lockfile ou un
      test a change », ou les tables devraient coincider et une difference est
      une nouvelle.

    Garde obligatoire : si `dbt/` a des modifications non commitees, le script
    sort en 1 **sans rien ecrire**. Un tree hash pris sur un dossier sale
    decrit du code qui n'existe dans aucun commit : le referentiel semblerait
    ancre a `HEAD` alors que la base a ete construite par autre chose, et la
    comparaison future serait verte contre un code que personne ne peut
    reconstruire. Mieux vaut pas de referentiel qu'un referentiel qui ment.
    """
    dirty = git_out("status", "--porcelain", "--", "dbt").strip()
    if dirty:
        listing = "\n    ".join(dirty.splitlines())
        fail(
            "dbt/ a des modifications non commitees : un tree hash pris ici decrirait "
            "du code qui n'existe dans aucun commit.\n"
            f"  chemins sales :\n    {listing}\n"
            "  Commiter (ou annuler) dbt/ et relancer. Rien n'a ete ecrit."
        )
    return {
        "_note": (
            "dbt_tree (HEAD:dbt) est le champ autoritatif : il couvre models/, tests/, "
            "dbt_project.yml, packages.yml, package-lock.yml, profiles.yml, tout ce qui "
            "decide de la sortie de dbt build. models_tree (HEAD:dbt/models) ne sert "
            "qu'au diagnostic quand dbt_tree diverge, pour separer 'un modele a change' "
            "de 'le lockfile ou un test a change'. Enregistres seulement avec dbt/ "
            "propre : le script sort en 1 sinon."
        ),
        "dbt_tree": git_out("rev-parse", "HEAD:dbt").strip(),
        "models_tree": git_out("rev-parse", "HEAD:dbt/models").strip(),
    }


def main():
    print("=" * 72)
    print("Referentiel pour la comparaison de reproductibilite par contenu")
    print("=" * 72)
    print(f"source        : {SRC}  (ATTACH READ_ONLY seulement)")
    print(f"sortie        : {OUT}")

    if not SRC.exists():
        fail(f"source absente : {SRC}. Il faut un `dbt build` dans dbt/ avant cette etape.")

    src_before = (SRC.stat().st_size, SRC.stat().st_mtime_ns)

    head = git_head()
    print(f"git HEAD      : {head}")

    # La garde vit ici, avant de toucher la base : si `dbt/` est sale le
    # script doit sortir sans avoir rien ecrit, et le JSON s'ecrit tout en bas.
    code = code_ref()
    print(f"dbt_tree      : {code['dbt_tree']}  (autoritatif)")
    print(f"models_tree   : {code['models_tree']}  (diagnostic)")

    con = duckdb.connect()
    try:
        con.execute(f"ATTACH '{SRC.as_posix()}' AS src (READ_ONLY)")
        reader = con.execute("select library_version, source_id from pragma_version()").fetchone()
        reader = {"library_version": reader[0], "source_id": reader[1], "pip_version": duckdb.__version__}
        print(f"lecteur       : DuckDB {reader['library_version']} (source_id "
              f"{reader['source_id']}), pip duckdb {reader['pip_version']}")

        # --- les 8 tables -----------------------------------------------------
        # Enumerees depuis le catalogue et pas depuis une liste ecrite a la
        # main : une table ajoutee ou retiree par un modele futur doit faire
        # sauter le comptage, pas passer inapercue parce que la liste n'a pas
        # ete mise a jour.
        names = [r[0] for r in con.execute(
            "select table_name from duckdb_tables() where database_name = 'src' order by table_name"
        ).fetchall()]
        if len(names) != EXPECTED_TABLES:
            fail(f"{len(names)} tables trouvees dans data/dvf.duckdb, "
                 f"{EXPECTED_TABLES} attendues : {', '.join(names)}")

        print(f"\n--- sommes de controle des {len(names)} tables ---")
        tables = {}
        for t in names:
            ref = f"src.main.{t}"
            entry = checksums(con, ref)
            entry["schema"] = schema_of(con, ref)
            # Une somme de controle a 0 ou NULL n'est pas une somme de
            # controle : c'est une table vide, ou un agregat qui n'a vu aucune
            # ligne. L'enregistrer comme referentiel signifierait que
            # n'importe quelle table vide lui correspondrait.
            if not entry["count"] or entry["bit_xor_hash"] in (None, 0) or entry["sum_hash"] in (None, 0):
                fail(f"{t} : somme de controle degeneree {entry}")
            tables[t] = entry
            print(f"  {t:<30} count {entry['count']:>9}  "
                  f"bit_xor {entry['bit_xor_hash']:>20}  {len(entry['schema'])} col")

        # --- echantillon de cellules, interroge par cle -----------------------
        print("\n--- echantillon de cellules ---")
        cell_sample = {}
        for label, dept, kind in (("75_appartement_2025", "75", "Appartement"),
                                  ("23_maison_2025", "23", "Maison"),
                                  ("971_maison_2025", "971", "Maison")):
            rows = rows_as_dicts(
                con,
                "select * from src.main.mart_prix_departement"
                " where code_departement = ? and type_local = ? and annee = 2025",
                [dept, kind],
            )
            if len(rows) != 1:
                fail(f"mart_prix_departement {label} : {len(rows)} lignes, 1 attendue.")
            cell_sample[f"mart_prix_departement_{label}"] = rows[0]
            print(f"  dept {dept:<4} {kind:<12} 2025  "
                  f"prix_m2_median {rows[0]['prix_m2_median']:>12.6f}  "
                  f"prix_m2_moyen {rows[0]['prix_m2_moyen']:>10.2f}")

        # Angers, toutes les annees avec la variation. La premiere annee a
        # `variation_yoy_pct` NULL : le NULL fait partie du referentiel autant
        # que les nombres, parce qu'il fige le comportement de la garde du lag
        # et pas seulement les valeurs.
        serie = rows_as_dicts(
            con,
            "select annee, prix_m2_median, prix_m2_median_prec, variation_yoy_pct"
            " from src.main.mart_prix_commune"
            " where code_commune = ? and type_local = ? order by annee",
            ["49007", "Appartement"],
        )
        if not serie:
            fail("mart_prix_commune 49007/Appartement : serie vide.")
        cell_sample["mart_prix_commune_49007_appartement_serie"] = serie
        for r in serie:
            yoy = "NULL" if r["variation_yoy_pct"] is None else f"{r['variation_yoy_pct']:+.6f}"
            print(f"  49007 Appartement {r['annee']}  "
                  f"prix_m2_median {r['prix_m2_median']:>11.6f}  yoy_pct {yoy}")

        # Extremes de `prix_m2_median` sur les deux marts : deux scalaires qui
        # bougent des que quelque chose change dans la queue de la
        # distribution, la ou les sommes de controle disent « different » mais
        # pas « de combien ».
        for t in ("mart_prix_departement", "mart_prix_commune"):
            lo, hi = con.execute(
                f"select min(prix_m2_median), max(prix_m2_median) from src.main.{t}"
            ).fetchone()
            cell_sample[f"{t}_prix_m2_median_min"] = lo
            cell_sample[f"{t}_prix_m2_median_max"] = hi
            print(f"  {t:<24} prix_m2_median min {lo:>12.6f}  max {hi:>13.6f}")

        # --- controle negatif ------------------------------------------------
        # Les trois memes nombres sur `mart_prix_departement` moins UNE ligne.
        # S'ils ne differaient pas tous les trois, le referentiel ne saurait
        # pas distinguer une base correcte d'une base a laquelle il manque une
        # ligne - et serait inutile exactement dans le cas pour lequel il a
        # ete construit.
        print("\n--- controle negatif : mart_prix_departement moins une ligne ---")
        base = tables["mart_prix_departement"]
        alt = checksums(
            con,
            "(select * from src.main.mart_prix_departement"
            " where prix_dept_sk <> (select min(prix_dept_sk) from src.main.mart_prix_departement))",
        )
        diff = {k: base[k] != alt[k] for k in ("count", "bit_xor_hash", "sum_hash")}
        for k in ("count", "bit_xor_hash", "sum_hash"):
            print(f"  {k:<13} referentiel {base[k]:>26}   "
                  f"moins une ligne {alt[k]:>26}   differe={diff[k]}")
        if not all(diff.values()):
            fail(f"controle negatif : les trois nombres ne different pas tous. {diff}")
        print("  les trois different : les sommes de controle voient la ligne manquante.")
    finally:
        con.close()

    # --- entrees brutes -------------------------------------------------------
    # La seule partie du referentiel comparee a l'octet, et c'est delibere :
    # ces fichiers ne sont pas produits par le pipeline - ils sont publies par
    # le Cerema (archive nationale DVF+) et l'Insee (COG). Une divergence ici,
    # c'est la source qui a change, et il faut la lire AVANT les tables.
    print("\n--- sha256 des entrees brutes ---")
    raw_files = [p for p in RAW_FILES if p.exists()]
    if len(raw_files) != len(RAW_FILES):
        missing = [str(p) for p in RAW_FILES if not p.exists()]
        fail(f"entrees brutes manquantes : {', '.join(missing)}. Relancer "
             "scripts/download_dvfplus.py (COG compris).")
    raw_inputs = []
    for p in raw_files:
        digest = sha256_of(p)
        if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            fail(f"{p.name} : sha256 malforme : {digest}")
        raw_inputs.append({"bytes": p.stat().st_size, "name": p.name, "sha256": digest})
        print(f"  {p.name:<36} {p.stat().st_size:>10} octets  {digest}")

    # --- ecriture -------------------------------------------------------------
    doc = {
        "cell_sample": cell_sample,
        # code_ref = CONTRE QUEL CODE, git_head = QUAND. Voir code_ref().
        "code_ref": code,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_head": head,
        "raw_inputs": raw_inputs,
        "reader": reader,
        "tables": tables,
        "writer_declared": WRITER_DECLARED,
    }
    # `newline="\n"` : LF explicite, le fichier est identique sous Windows et
    # Linux. `sort_keys` : deux executions doivent produire le meme ordre de
    # cles, ou la comparaison montrerait des differences qui n'en sont pas.
    with open(OUT, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(doc, fh, indent=2, sort_keys=True)
        fh.write("\n")

    # --- garde-fou READ_ONLY, verifie -----------------------------------------
    src_after = (SRC.stat().st_size, SRC.stat().st_mtime_ns)
    if src_after != src_before:
        fail(f"data/dvf.duckdb a change : avant={src_before} apres={src_after}")
    if Path(f"{SRC}.wal").exists():
        fail("data/dvf.duckdb.wal a ete cree : la source a ete ouverte en ecriture.")
    print(f"\nsource intacte : {src_after[0]} octets, mtime inchange, aucun .wal.")

    print(f"\nOK - {OUT.name} ecrit : {len(tables)} tables, "
          f"{len(cell_sample)} entrees d'echantillon, {len(raw_inputs)} entrees brutes, "
          f"{OUT.stat().st_size} octets.")


if __name__ == "__main__":
    main()
