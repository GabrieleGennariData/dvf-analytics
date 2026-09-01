#!/usr/bin/env python
"""Export des marts vers `app/app.duckdb`, la base legere lue par Streamlit.

Trois tables exportees ENTIERES, aucune colonne retiree : `prix_m2_moyen`
n'existe que dans le mart departement et alimente un des quatre KPI.

Quatre contraintes, avec leur raison :

1. `data/dvf.duckdb` s'ouvre TOUJOURS en READ_ONLY, et le script mesure taille
   et mtime avant et apres : la contrainte se verifie au lieu de se declarer.
2. Copie pure, `select *` : une liste de colonnes reecrite a la main serait une
   seconde definition libre de diverger du mart.
3. `app/app.duckdb` est supprime et recree de zero. Le mou interne d'un fichier
   DuckDB ne se rend pas tout seul, et sur un fichier commite dans git il
   devient permanent. Du coup `CREATE TABLE` sec : si la table existe deja,
   c'est que le fichier n'a pas ete recree, et le script doit echouer.
4. Le lecteur est le paquet pip `duckdb`, celui qu'epingle requirements.txt et
   qu'utilisera Streamlit Cloud.

La verification finale est par CONTENU, jamais par octets : deux runs
produisent deux fichiers differents a l'octet, et c'est attendu. Ce qui doit
rester identique : comptages, sommes de controle et schema.

Usage : python scripts/export_app_db.py
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import duckdb

# Chemins resolus depuis le fichier, pas depuis le cwd : le script doit
# fonctionner depuis la racine du depot, depuis scripts/ et depuis le
# quickstart du README.
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data" / "dvf.duckdb"
DST = ROOT / "app" / "app.duckdb"

TABLES = ("mart_prix_departement", "mart_prix_commune", "dim_commune")

# Limite de presentation, pas technique : les douze annees portent l'export a
# une vingtaine de Mo. Plafond a 40 Mo plutot que de rogner les donnees.
# GitHub avertit a 50 Mo, et chaque commit du blob ajoute sa taille entiere a
# l'historique.
MAX_BYTES = 40 * 1024 * 1024

# Comptages attendus, ancrage externe : sans eux, un export de trois tables
# vides passerait la comparaison source / destination. Mesures sur la
# livraison entiere : grille departement saturee a 2 328 cellules (97 x 12 x
# 2), mart commune 449 027, dim 36 912 communes du COG 2026.
EXPECTED_COUNTS = {
    "mart_prix_departement": 2_328,
    "mart_prix_commune": 449_027,
    "dim_commune": 36_912,
}


def fail(msg):
    """Critere en echec = s'arreter, jamais forcer le pass."""
    print(f"\n[ECHEC] {msg}", file=sys.stderr)
    raise SystemExit(1)


def fingerprint(con, ref):
    """Empreinte du CONTENU d'une table, independante de l'ordre des lignes.

    Trois nombres et pas un, parce que pris seuls chacun a un trou :
    - `count(*)` ne voit pas une ligne changee ;
    - `bit_xor` est insensible a l'ordre mais aussi aux paires de doublons,
      qui s'annulent entre elles ;
    - `sum` distingue les multiplicites, et passe en HUGEINT parce que la
      somme de 188 664 hash a 64 bits deborde BIGINT.
    Ensemble ils couvrent les trois facons dont une copie peut diverger :
    lignes en moins, lignes differentes, lignes repetees.
    """
    return con.execute(
        f"select count(*), bit_xor(hash(t)), sum(hash(t)::HUGEINT) from {ref} t"
    ).fetchone()


def schema_of(con, ref):
    """(nom, type) dans l'ordre de declaration. L'ordre fait partie du schema :
    un `select *` qui inverserait deux colonnes du meme type produirait une
    table avec le meme jeu de colonnes et les donnees echangees."""
    return [(r[0], r[1]) for r in con.execute(f"describe {ref}").fetchall()]


def main():
    print("=" * 72)
    print("Export des marts vers app/app.duckdb")
    print("=" * 72)
    print(f"lecteur       : pip duckdb {duckdb.__version__}  (la version epinglee dans requirements.txt)")
    print(f"source        : {SRC}")
    print(f"destination   : {DST}")

    if not SRC.exists():
        fail(f"source absente : {SRC}. Il faut un `dbt build` dans dbt/ avant cette etape.")

    # Garde-fou 1, partie 1 : etat de la source AVANT. Recompare a la fin.
    src_before = (SRC.stat().st_size, SRC.stat().st_mtime_ns)
    print(f"source octets : {src_before[0]}")

    # --- Garde-fou 3 : le fichier de destination se recree de zero -----------
    # Les artefacts collateraux de DuckDB partent aussi : un `.wal` orphelin
    # d'une session interrompue serait rejoue au premier open et ramenerait
    # des donnees d'un run precedent.
    removed = []
    for leftover in (DST, Path(f"{DST}.wal")):
        if leftover.exists():
            leftover.unlink()
            removed.append(leftover.name)
    tmpdir = Path(f"{DST}.tmp")
    if tmpdir.is_dir():
        shutil.rmtree(tmpdir)
        removed.append(f"{tmpdir.name}/")
    DST.parent.mkdir(parents=True, exist_ok=True)
    removed_note = f"supprimes {', '.join(removed)}" if removed else "aucun residu a supprimer"
    print(f"recree de 0   : {removed_note}")

    # --- Export --------------------------------------------------------------
    print("\n--- export ---")
    con = duckdb.connect(str(DST))
    try:
        # Garde-fou 1, partie 2 : la source entre en READ_ONLY. La destination
        # est la base par defaut de la connexion, donc les tables neuves
        # s'ecrivent sans qualificateur et il n'y a pas moyen de se tromper de
        # sens.
        con.execute(f"ATTACH '{SRC.as_posix()}' AS src (READ_ONLY)")
        engine = con.execute("select library_version, source_id from pragma_version()").fetchone()
        print(f"moteur actif  : DuckDB {engine[0]} (source_id {engine[1]})")

        for t in TABLES:
            # Garde-fou 2 : `select *`. Garde-fou 3 : `CREATE TABLE` sec - sur
            # un fichier tout juste cree la table ne peut pas exister, et si
            # elle existait le script doit s'arreter au lieu d'ecraser en
            # silence.
            con.execute(f"CREATE TABLE {t} AS SELECT * FROM src.main.{t}")
            n = con.execute(f"select count(*) from {t}").fetchone()[0]
            print(f"  {t:<24} {n:>9} lignes")

        # --- Verification par contenu, la source encore attachee -------------
        # La faire ici est ce qui rend la comparaison une comparaison : les
        # deux tables sont dans la meme session et le meme moteur calcule les
        # deux empreintes.
        print("\n--- comparaison par contenu contre la source ---")
        for t in TABLES:
            got = fingerprint(con, t)
            want = fingerprint(con, f"src.main.{t}")
            if got != want:
                fail(
                    f"{t} : contenu divergent de la source.\n"
                    f"  app  (count, bit_xor, sum) = {got}\n"
                    f"  src  (count, bit_xor, sum) = {want}"
                )
            if got[0] != EXPECTED_COUNTS[t]:
                fail(
                    f"{t} : {got[0]} lignes, {EXPECTED_COUNTS[t]} attendues."
                    " Source et destination coincident mais ne sont pas ce que"
                    " cette etape doit exporter."
                )
            s_got = schema_of(con, t)
            s_want = schema_of(con, f"src.main.{t}")
            if s_got != s_want:
                fail(f"{t} : schema divergent.\n  app = {s_got}\n  src = {s_want}")
            print(f"  {t:<24} count {got[0]:>9}  bit_xor {got[1]:>20}  schema {len(s_got)} col  OK")

        # CHECKPOINT avant de fermer : force le WAL dans le fichier. Sans lui,
        # la taille mesuree juste apres serait celle d'un fichier incomplet et
        # le critere « < 20 Mo » serait mesure sur du vide.
        con.execute("DETACH src")
        con.execute("CHECKPOINT")
    finally:
        con.close()

    # --- Garde-fou 1, verification empirique : la source n'a pas ete touchee -
    src_after = (SRC.stat().st_size, SRC.stat().st_mtime_ns)
    if src_after != src_before:
        fail(
            f"data/dvf.duckdb a change pendant l'export. avant={src_before}"
            f" apres={src_after}. READ_ONLY n'a pas tenu."
        )
    if Path(f"{SRC}.wal").exists():
        fail("data/dvf.duckdb.wal a ete cree : la source a ete ouverte en ecriture.")
    print("\nsource intacte : taille et mtime inchanges, aucun .wal cree.")

    # --- Taille ---------------------------------------------------------------
    size = DST.stat().st_size
    print(f"\napp/app.duckdb : {size} octets ({size / 1024 / 1024:.2f} MiB), limite {MAX_BYTES} octets")
    if size > MAX_BYTES:
        fail(f"app/app.duckdb depasse les 20 Mo : {size} octets.")

    # --- Reouverture en read_only : la modalite de Streamlit Cloud ------------
    # Essayee ici et pas la-bas, parce qu'un fichier qui s'exporte mais ne se
    # rouvre pas en lecture seule echouerait en production et pas a cette etape.
    print("\n--- reouverture en read_only (la modalite de l'app sur Streamlit Cloud) ---")
    ro = duckdb.connect(str(DST), read_only=True)
    try:
        ro.execute(f"ATTACH '{SRC.as_posix()}' AS src (READ_ONLY)")
        for t in TABLES:
            got = fingerprint(ro, t)
            want = fingerprint(ro, f"src.main.{t}")
            if got != want or schema_of(ro, t) != schema_of(ro, f"src.main.{t}"):
                fail(f"{t} : divergent de la source apres la reouverture en read_only.")
        print("  les 3 tables relisibles en lecture seule, contenu et schema toujours alignes")

        # Les colonnes que le voyage pouvait abimer en silence.
        # `code_departement` et `code_commune` VARCHAR : un passage par un
        # entier mangerait les zeros de tete (01 -> 1), casserait la Corse
        # (2A/2B ne sont pas des nombres) et les DOM a 3 chiffres. Le join de
        # la choroplethe est sur chaine : il echouerait en silence, carte grise.
        types = dict(schema_of(ro, "mart_prix_commune"))
        for col in ("code_commune", "code_departement"):
            if types[col] != "VARCHAR":
                fail(f"mart_prix_commune.{col} n'est pas VARCHAR mais {types[col]}.")
        leading_zeros, corsica, dom = ro.execute(
            "select count(*) filter (where code_commune like '0%'),"
            "       count(*) filter (where code_departement in ('2A','2B')),"
            "       count(*) filter (where length(code_departement) = 3)"
            " from mart_prix_commune"
        ).fetchone()
        if not (leading_zeros and corsica and dom):
            fail(f"codes geographiques degrades : zeros={leading_zeros} corse={corsica} dom={dom}.")
        print(f"  codes VARCHAR intacts : {leading_zeros} cellules a zero de tete, "
              f"{corsica} en Corse (2A/2B), {dom} dans les DOM")

        # `prix_m2_moyen` arrondie a 2 decimales : la seule non-determinisme du
        # projet qu'on ait pu retirer, et ce qui rend praticable la comparaison
        # par contenu du referentiel. Si le voyage la ramenait a 17 chiffres,
        # cette comparaison redeviendrait inverifiable.
        not_rounded = ro.execute(
            "select count(*) from mart_prix_departement"
            " where prix_m2_moyen is not null and prix_m2_moyen <> round(prix_m2_moyen, 2)"
        ).fetchone()[0]
        if not_rounded:
            fail(f"prix_m2_moyen n'est plus arrondie a 2 decimales sur {not_rounded} lignes.")
        print("  prix_m2_moyen : 0 lignes hors de l'arrondi a 2 decimales")
    finally:
        ro.close()

    # --- Empreinte finale, pour la comparaison entre runs ---------------------
    # Imprimee dans un bloc a part et en chiffres nus : c'est ce que deux runs
    # consecutifs doivent avoir d'identique. Les octets du fichier non, et ce
    # n'est pas un echec.
    print("\n--- CONTENT-FINGERPRINT ---")
    fp = duckdb.connect(str(DST), read_only=True)
    try:
        for t in TABLES:
            c, x, s = fingerprint(fp, t)
            cols = ",".join(f"{n}:{ty}" for n, ty in schema_of(fp, t))
            print(f"FP {t} count={c} bit_xor={x} sum={s}")
            print(f"FP {t} schema={cols}")
    finally:
        fp.close()
    print("--- fin CONTENT-FINGERPRINT ---")

    print(f"\nOK - app/app.duckdb regenere : {len(TABLES)} tables, {size} octets.")


if __name__ == "__main__":
    main()
