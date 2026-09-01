"""Telechargement de DVF+ open-data (Cerema), edition nationale CSV.

Source : jeu "DVF+ open-data" sur data.gouv.fr, produit par le Cerema a partir
des donnees DGFiP, Licence Ouverte 2.0, heberge sur le Box du Cerema :

    https://cerema.app.box.com/v/dvfplus-opendata

Le script parcourt le dossier partage par NOMS (edition, format, National),
telecharge les volumes 7z, les concatene et extrait les CSV departementaux
dans data/raw_dvfplus/. Naviguer par noms plutot que par identifiants Box lui
permet de survivre a une nouvelle edition : il suffit de changer EDITION.

Usage :
    python scripts/download_dvfplus.py            # edition par defaut
    python scripts/download_dvfplus.py --edition avril_2026

Dependance : py7zr, volontairement hors de requirements.txt, qui ne decrit que
l'application deployee.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import py7zr

SHARED_NAME = "pgoyov5dddimtnk5j6akbfl79mr4jrep"
VIEW_URL = "https://cerema.app.box.com/v/dvfplus-opendata/folder/{}"
DOWNLOAD_URL = (
    "https://cerema.app.box.com/index.php"
    "?rm=box_v2_download_shared_file&shared_name=" + SHARED_NAME + "&file_id={}"
)
ROOT_FOLDER_ID = "77228357248"  # dossier racine "dvfplus" du partage
USER_AGENT = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

# Plancher de vraisemblance d'un volume : les volumes de la livraison
# nationale pesent des centaines de megaoctets. Tout ce qui est en dessous
# est une page d'erreur, pas des donnees.
MIN_VOLUME_BYTES = 1_000_000
# Signature d'une archive 7z, verifiee avant extraction : deux volumes
# concatenes qui ne la portent pas ne sont pas une archive tronquee, ce sont
# des octets qui ne sont pas les bons.
SEVENZIP_MAGIC = b"7z\xbc\xaf\x27\x1c"

EDITION = "avril_2026"  # edition 2026.1 (ED251), actes 2014 - 2025
DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "raw_dvfplus"

# Le referentiel des noms de communes (COG Insee) : DVF+ ne livre que des
# codes, dim_commune a besoin d'un nom lisible. Fichier officiel, ~3,6 Mo.
COG_URL = "https://www.insee.fr/fr/statistiques/fichier/8740222/v_commune_2026.csv"
COG_DEST = Path(__file__).resolve().parent.parent / "data" / "raw_cog" / "v_commune_2026.csv"


def open_with_retry(url: str, timeout: int, attempts: int = 5):
    """urlopen avec tentatives et pause croissante (15/30/60/120 s).

    Necessaire, pas decoratif : un replay complet du quickstart depuis un
    clone a froid est tombe sur des 403 INTERMITTENTS de Box sur le premier
    volume - la meme URL repondait 200 quelques minutes plus tard, quelle que
    soit la forme de la requete (mesure : UA courts et complets, avec et sans
    cookies, tous 200 ensuite). Ressemble a une limitation de debit par IP et
    par fichier apres des telechargements repetes : les pauses sont donc
    longues, pas cosmetiques.
    """
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        req = urllib.request.Request(url, headers=USER_AGENT)
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except (urllib.error.HTTPError, urllib.error.URLError, OSError) as exc:
            last = exc
            if attempt < attempts:
                pause = min(15 * 2 ** (attempt - 1), 120)
                print(f"  tentative {attempt} echouee ({exc}) - nouvelle dans {pause}s")
                time.sleep(pause)
    raise LookupError(f"echec apres {attempts} tentatives : {last}")


def list_items(folder_id: str) -> list[tuple[str, str]]:
    """Retourne les (typedID, nom) des elements d'un dossier Box partage.

    La page de la visionneuse embarque la configuration JSON du dossier ;
    on en extrait les paires id/nom par expression reguliere plutot que par
    l'API Box, qui exigerait un jeton d'authentification pour un contenu
    pourtant public.
    """
    html = open_with_retry(VIEW_URL.format(folder_id), timeout=120).read().decode("utf-8", "replace")
    pairs = re.findall(r'\{"typedID":"([df]_\d+)",.{0,4000}?"name":"([^"]*)"', html)
    seen: set[tuple[str, str]] = set()
    ordered = []
    for pair in pairs:
        if pair not in seen:
            seen.add(pair)
            ordered.append(pair)
    return ordered


def resolve_path(names: list[str]) -> str:
    """Descend l'arborescence du partage par noms et retourne l'id du dossier final."""
    folder_id = ROOT_FOLDER_ID
    for name in names:
        matches = [tid for tid, n in list_items(folder_id) if n == name]
        if not matches:
            raise SystemExit(f"dossier '{name}' introuvable sous {folder_id}")
        folder_id = matches[0].removeprefix("d_")
    return folder_id


def download(file_id: str, dest: Path) -> None:
    """Telecharge un volume, avec un dernier recours par curl, et VERIFIE.

    curl est l'outil avec lequel la chaine Box a ete validee et il n'a jamais
    recu de 403 la ou urllib en recevait par rafales : si les tentatives
    urllib s'epuisent, on tente curl avant d'abandonner. Dependance
    facultative - absente, l'echec urllib redevient fatal.

    La VERIFICATION n'est pas du zele. Sans `--fail`, curl ecrit le corps de
    la reponse d'erreur dans le fichier de sortie et sort en 0 : lors du
    premier replay a froid, un volume de 248 Mo est arrive sous la forme
    d'une page HTML de 1 990 octets, et l'echec ne s'est manifeste que deux
    etapes plus loin, a l'ouverture de l'archive concatenee - au mauvais
    endroit, avec le mauvais message. On compare donc les octets ecrits a la
    taille annoncee par le serveur, et un volume anormalement petit est une
    erreur ici, pas plus tard.
    """
    url = DOWNLOAD_URL.format(file_id)
    expected = None
    try:
        with open_with_retry(url, timeout=600) as resp, open(dest, "wb") as out:
            # Box annonce la taille reelle dans un en-tete a lui quand la
            # reponse est chunked : les deux sont acceptes, le premier present
            # fait foi.
            expected = resp.headers.get("x-box-original-content-length") \
                or resp.headers.get("content-length")
            while chunk := resp.read(1 << 20):
                out.write(chunk)
    except LookupError as exc:
        print(f"  {exc} - dernier recours via curl")
        # `--fail` : sans lui curl ecrit la page d'erreur et sort en 0.
        rc = subprocess.run(
            ["curl", "-sL", "--fail", "--retry", "3", "--retry-delay", "30",
             "-o", str(dest), url]
        ).returncode
        if rc != 0:
            raise SystemExit(f"telechargement impossible, aussi via curl (rc={rc})")

    size = dest.stat().st_size if dest.exists() else 0
    if expected is not None and size != int(expected):
        raise SystemExit(
            f"{dest.name}: {size} octets recus, {expected} annonces par le serveur."
        )
    if size < MIN_VOLUME_BYTES:
        raise SystemExit(
            f"{dest.name}: {size} octets - trop petit pour un volume de la livraison. "
            "C'est probablement une page d'erreur : Box limite le debit par IP, "
            "reessayer plus tard."
        )
    print(f"  {dest.name}: {size:,} octets")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--edition", default=EDITION)
    args = parser.parse_args()

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Sensible au cache : relancer ne coute rien. Si la livraison est deja extraite, tout le telechargement saute -
    # ce qui compte aussi pour la politesse envers Box (voir open_with_retry).
    delivered = DATA_DIR / "1_DONNEES_LIVRAISON"
    if delivered.is_dir() and len(list(delivered.glob("dvf_plus_d*.csv"))) >= 97:
        print(f"livraison deja extraite ({delivered}), telechargement saute")
    else:
        national_id = resolve_path([args.edition, "csv", "National"])
        parts = sorted(
            (name, tid.removeprefix("f_"))
            for tid, name in list_items(national_id)
            if re.search(r"\.7z\.\d{3}$", name)
        )
        if not parts:
            raise SystemExit("aucun volume .7z.NNN dans le dossier National")

        print(f"edition {args.edition}: {len(parts)} volumes")
        for name, file_id in parts:
            download(file_id, DATA_DIR / name)

        # Les volumes .001/.002 sont une decoupe brute : la concatenation
        # reconstitue une archive 7z valide. Celle-ci contient UNE archive 7z
        # interne (emballage de livraison du Cerema), qui contient a son tour
        # 1_DONNEES_LIVRAISON/dvf_plus_dXX.csv - un CSV par departement,
        # separateur "|" (voir stg_dvfplus__mutations.sql).
        archive = DATA_DIR / re.sub(r"_part\.7z\.\d{3}$", ".7z", parts[0][0])
        with open(archive, "wb") as out:
            for name, _ in parts:
                out.write((DATA_DIR / name).read_bytes())

        # Verification avant extraction : py7zr dirait « not a 7z file », ce
        # qui envoie chercher le probleme dans l'archive alors qu'il est dans
        # le telechargement. Ce controle-ci nomme la vraie cause.
        with open(archive, "rb") as fh:
            if fh.read(len(SEVENZIP_MAGIC)) != SEVENZIP_MAGIC:
                raise SystemExit(
                    f"{archive.name} n'est pas une archive 7z : la concatenation "
                    "des volumes n'a pas produit ce qu'elle devait. Supprimer "
                    f"{DATA_DIR} et relancer."
                )

        with py7zr.SevenZipFile(archive) as outer:
            outer.extractall(DATA_DIR / "_outer")
        inner = next((DATA_DIR / "_outer").glob("*.7z"))
        with py7zr.SevenZipFile(inner) as arch:
            arch.extractall(DATA_DIR)
        print("extraction terminee:", delivered)

    # --- COG Insee ---------------------------------------------------------
    if COG_DEST.exists():
        print(f"COG deja present: {COG_DEST}")
    else:
        COG_DEST.parent.mkdir(parents=True, exist_ok=True)
        with open_with_retry(COG_URL, timeout=120) as resp, open(COG_DEST, "wb") as out:
            out.write(resp.read())
        print(f"COG telecharge: {COG_DEST} ({COG_DEST.stat().st_size:,} octets)")


if __name__ == "__main__":
    main()
