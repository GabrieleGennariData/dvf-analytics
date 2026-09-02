#!/usr/bin/env python
"""Tableau de bord DVF : prix immobiliers francais par departement et commune.

Ne lit QUE `app/app.duckdb`, en lecture seule : la base de travail fait une
dizaine de Gio et n'existe pas sur Streamlit Cloud. Le chemin se resout depuis
`__file__` et non depuis le cwd.

Aucun calcul de prix ici : `prix_m2` se definit dans l'intermediate et les
marts en transportent les agregations. La seule fenetre SQL du fichier
reconstruit la variation annuelle au grain departement, que le mart n'expose
pas, avec la meme garde d'adjacence que le mart commune.

Ce que ce tableau de bord declare au lieu de cacher : la population de prix
n'est pas celle du volume ; le seuil du top-N vaut pour les DEUX annees ; les
trois raisons d'une variation absente restent distinctes ; quatre departements
sans donnees le sont par lacune de la source.

Lancement : streamlit run app/streamlit_app.py
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

HERE = Path(__file__).resolve().parent
DB = HERE / "app.duckdb"
GEOJSON = HERE / "departements.geojson"

# Seuil de PRESENTATION, pas de modelisation : applique dans le mart il aurait
# efface des lignes pour tous les consommateurs, alors qu'il ne sert qu'au
# top-N. Le mart expose `n_ventes_eligible`, donc le seuil reste verifiable de
# l'exterieur.
THRESHOLD = 30
TOP_N = 15

# Les 4 departements avec un polygone mais sans donnees. Lacune de la source et
# non filtre de l'app : ils doivent rester distinguables d'une cellule videe
# par une selection.
NO_DATA = {
    "57": "Moselle : droit local, livre foncier, hors périmètre DVF",
    "67": "Bas-Rhin : droit local, livre foncier, hors périmètre DVF",
    "68": "Haut-Rhin : droit local, livre foncier, hors périmètre DVF",
    "976": "Mayotte : non couverte par la source",
}
NO_DATA_COLOR = "#b9b2a6"

# Les deux widgets qui se parlent. Le selecteur de departement est le seul des
# trois a porter une cle d'etat : le clic sur la carte lui pose sa valeur.
DEPT_KEY = "filtre_departement"
MAP_KEY = "carte_departements"
# Sans cette trace, un clic sur un departement sans donnees ne laisserait que
# le silence, qui se lit comme une panne.
CLICK_NOTICE_KEY = "_clic_departement_sans_donnees"

# Contour du departement selectionne. Rouge vif pour rester lisible aux deux
# bouts de Viridis, ce que ni un blanc ni un noir ne font.
SELECTED_COLOR = "#ff1744"
SELECTED_WIDTH = 2.5
BORDER_COLOR = "#ffffff"
BORDER_WIDTH = 0.5

# Les trois departements ou « commune » signifie arrondissement. Pour chacun :
# nom de la ville, plage des codes reels, et le code que la source ne contient
# PAS, qui est le fait qui surprend.
ARRONDISSEMENT = {
    "75": ("Paris", "75101-75120", "75056"),
    "69": ("Lyon", "69381-69389", "69123"),
    "13": ("Marseille", "13201-13216", "13055"),
}

# Bornes explicites sur la metropole, a la place de `fitbounds="locations"` :
# avec les DOM parmi les `locations` le cadrage irait de -53 a +55 et reduirait
# la metropole a un timbre-poste. La latitude minimale garde la Corse, la
# longitude maximale Menton.
BOUNDS_METROPOLE = {"lonaxis_range": [-5.4, 9.8], "lataxis_range": [41.2, 51.2]}

# Les trois colonnes de comptage du mart, DEFINIES dans
# `mart_prix_departement.sql` et en amont dans `int_dvfplus__mutations.sql`.
# Transcrites ici parce que la note sous les KPI doit etre derivable du SQL, et
# parce que le nom de l'une des trois trompe :
#
#   n_ventes_total        = count(*), aucun filtre de prix. Le volume.
#   n_ventes_eligible     = count(*) filter (is_price_plausible). ATTENTION,
#                           c'est plausible et non eligible : la plus etroite
#                           des trois populations, denominateur de la mediane.
#   n_ventes_prix_exclues = les eligibles ecartees par la bande.
#
# Le perimetre du mart etant mono-logement, le volume coincide aujourd'hui avec
# la population eligible, et l'ecart avec le denominateur n'a plus qu'une cause
# visible : les 37 383 ventes hors observation de marche.
FOOTNOTE = (
    "Ce tableau de bord compte deux populations, et ne les mélange jamais.\n\n"
    "**Ventes (total)** est le volume : toutes les ventes valides de la cellule.\n\n"
    "**Base du prix médian** est le dénominateur de la médiane : les seules ventes dont "
    "le prix est une observation de marché.\n\n"
    "Entre les deux se trouvent les ventes au prix calculable mais symbolique ou hors "
    "d'échelle, en dehors de [100 ; 40 000] €/m² ou de [9 ; 1 500] m². Sur l'ensemble de "
    "la table elles sont 37 383, soit 0,38 % des éligibles. Les mutations qui portent "
    "plusieurs biens, elles, n'entrent pas du tout dans ce tableau : leur prix couvre "
    "plusieurs logements et ne se ramène au m² d'aucun.\n\n"
    "Aucune ligne n'est supprimée des données. Ce sont des drapeaux, et c'est pourquoi "
    "le volume reste entier."
)


# ---------------------------------------------------------------------------
# Formatage numerique : UNE convention fr-FR, deux fonctions.
#
# Milliers separes par une espace fine insecable, decimales par une VIRGULE.
# En francais `1,544` se lit comme une decimale, donc ecrire `1,544` pour mille
# cinq cent quarante-quatre montre un nombre faux de trois ordres de grandeur,
# et le montre de facon parfaitement lisible. Aucun format inline sur un nombre
# affiche ; ce que les helpers n'atteignent pas, les graduations de plotly, se
# regle par `separators` sur le layout.
# ---------------------------------------------------------------------------

NBSP_THOUSANDS = " "  # espace fine insecable, le separateur des milliers en francais
NBSP_UNIT = " "  # espace insecable, entre nombre et unite : en francais aussi avant %


def fmt_nb(x, decimals=0, unit=None):
    """Entiers, montants et mesures. `fmt_nb(12592, unit="€/m²")` -> `12 592 €/m²`."""
    if x is None or pd.isna(x):
        return "-"
    s = "{:,.{}f}".format(x, decimals)          # notation en-US : 12,592.35
    s = s.replace(",", NBSP_THOUSANDS).replace(".", ",")   # -> fr-FR : 12 592,35
    return s if unit is None else s + NBSP_UNIT + unit


def fmt_pct(x, decimals=2, sign=False):
    """Pourcentages. `fmt_pct(15.2384)` -> `15,24 %` ; avec `sign=True` -> `+15,24 %`.
    L'espace avant le signe pour cent est la regle typographique francaise."""
    if x is None or pd.isna(x):
        return "-"
    s = "{:+.{}f}".format(x, decimals) if sign else "{:.{}f}".format(x, decimals)
    return s.replace(".", ",") + NBSP_UNIT + "%"


def plur(n, singular, plural_form):
    """Accord en nombre pour les comptages calcules a l'execution.

    Les phrases de ce tableau de bord comptent des cellules et des ventes qui
    peuvent tres bien etre UNE, et « 1 n'ont pas de variation » se denonce
    comme genere."""
    return singular if abs(n) == 1 else plural_form


# ---------------------------------------------------------------------------
# Acces aux donnees. Ces fonctions prennent `con` en premier argument et ne
# touchent pas Streamlit : elles s'appellent depuis un test ou un terminal.
# ---------------------------------------------------------------------------

# Fenetre au grain departement, avec la garde d'adjacence du mart commune : le
# mart departement n'expose pas la variation annuelle. Un `lag()` nu sauterait
# en silence a l'annee precedente DISPONIBLE et comparerait 2025 a 2022.
DEPT_SERIES = """
    select
        code_departement, annee, type_local,
        n_ventes_total, n_ventes_eligible, n_ventes_prix_exclues,
        prix_m2_median, prix_m2_moyen, surface_mediane,
        lag(annee)          over w as annee_prec,
        lag(prix_m2_median) over w as prix_m2_median_prec
    from mart_prix_departement
    window w as (partition by code_departement, type_local order by annee)
"""

# Meme forme au grain commune. Le mart porte deja la variation annuelle ; on ne
# reconstruit que le `n_ventes_eligible` de l'annee precedente, qui sert a
# appliquer le seuil aux deux annees.
COMMUNE_SERIES = """
    select
        code_commune, code_departement, annee, type_local,
        n_ventes_total, n_ventes_eligible, n_ventes_prix_exclues,
        prix_m2_median, prix_m2_median_prec, variation_yoy_pct,
        lag(annee)             over w as annee_prec,
        lag(n_ventes_eligible) over w as n_ventes_eligible_prec_raw
    from mart_prix_commune
    window w as (partition by code_commune, type_local order by annee)
"""


def connect(db_path=DB):
    """Lecture seule, toujours. L'app n'a aucune raison d'ecrire."""
    return duckdb.connect(str(db_path), read_only=True)


def available_years(con):
    return [r[0] for r in con.execute(
        "select distinct annee from mart_prix_departement order by annee").fetchall()]


def departments_with_data(con):
    return [r[0] for r in con.execute(
        "select distinct code_departement from mart_prix_departement order by 1").fetchall()]


def department_kpi(con, annee, type_local, code_departement):
    """Les 4 KPI d'une cellule, plus la variation annuelle de la mediane et le
    contexte qui la rend lisible. Une seule ligne, ou None si la cellule
    n'existe pas."""
    df = con.execute(
        "with series as (" + DEPT_SERIES + ")"
        " select *,"
        "   case when annee_prec = annee - 1"
        "        then 100.0 * (prix_m2_median - prix_m2_median_prec) / prix_m2_median_prec"
        "   end as variation_yoy_pct"
        " from series"
        " where annee = ? and type_local = ? and code_departement = ?",
        [annee, type_local, code_departement],
    ).fetchdf()
    return None if df.empty else df.iloc[0]


def base_year(con):
    """La premiere annee du mart, lue A L'EXECUTION. Jamais une annee ecrite en
    dur : le pipeline n'a aucun filtre d'annee, donc une annee livree en plus
    entre toute seule et une constante deviendrait une etiquette fausse."""
    return con.execute("select min(annee) from mart_prix_departement").fetchone()[0]


def department_map(con, annee, type_local):
    """Une ligne par departement avec donnees, pour la choroplethe.

    Porte aussi la variation par rapport a l'ANNEE DE BASE, qui compare toute la
    fenetre la ou le delta du premier KPI compare deux annees adjacentes. Se
    calcule sur la MEDIANE. Aucune garde d'adjacence : la comparaison se fait
    avec une annee fixe et nommee a l'ecran."""
    return con.execute(
        "with base as ("
        "   select code_departement, type_local, prix_m2_median as prix_m2_median_base"
        "   from mart_prix_departement where annee = ?)"
        " select m.code_departement, m.prix_m2_median, m.prix_m2_moyen, m.surface_mediane,"
        "        m.n_ventes_total, m.n_ventes_eligible, m.n_ventes_prix_exclues,"
        "        b.prix_m2_median_base,"
        "        case when b.prix_m2_median_base is not null and m.prix_m2_median is not null"
        "             then 100.0 * (m.prix_m2_median - b.prix_m2_median_base)"
        "                  / b.prix_m2_median_base"
        "        end as variation_vs_base_pct"
        " from mart_prix_departement m"
        " left join base b using (code_departement, type_local)"
        " where m.annee = ? and m.type_local = ?"
        " order by m.code_departement",
        [base_year(con), annee, type_local],
    ).fetchdf()


def dept_cells_below_threshold(con, threshold=THRESHOLD):
    """Les cellules departement sous le seuil, que le tableau ANNOTE au lieu de
    les cacher. Calculees et non recopiees : sur le build courant l'ensemble est
    vide (minimum 84 ventes), et la fonction le prouve a chaque edition."""
    return con.execute(
        "select code_departement, annee, type_local, n_ventes_eligible"
        " from mart_prix_departement where n_ventes_eligible < ?"
        " order by n_ventes_eligible, code_departement, annee",
        [threshold],
    ).fetchdf()


def top_n_candidates(con, annee=None, type_local=None, code_departement=None,
                     threshold=THRESHOLD, threshold_on_both_years=True, limit=None):
    """Les candidates au top-N par variation annuelle, au grain commune.
    FONCTION PURE : sans filtres, retourne l'ensemble complet des candidates.

    `threshold_on_both_years` est le coeur de l'etape : applique a la seule
    annee courante, 7 019 candidates sur 55 180 ont l'annee precedente sous le
    seuil, avec un minimum d'UNE vente. Applique aux deux annees, 48 161.
    Le parametre reste expose pour pouvoir re-mesurer ce delta."""
    where, params = [], []
    if annee is not None:
        where.append("annee = ?")
        params.append(annee)
    if type_local is not None:
        where.append("type_local = ?")
        params.append(type_local)
    if code_departement is not None:
        where.append("code_departement = ?")
        params.append(code_departement)

    sql = (
        "with series as (" + COMMUNE_SERIES + "),"
        " guarded as ("
        "   select * exclude (n_ventes_eligible_prec_raw),"
        "     case when annee_prec = annee - 1 then n_ventes_eligible_prec_raw end"
        "       as n_ventes_eligible_prec"
        "   from series)"
        " select g.*, d.nom_commune"
        " from guarded g left join dim_commune d using (code_commune)"
        " where g.variation_yoy_pct is not null and g.n_ventes_eligible >= ?"
    )
    if threshold_on_both_years:
        sql += " and g.n_ventes_eligible_prec >= ?"
    # Les filtres d'UI vont en queue, apres les seuils : ainsi la meme fonction,
    # appelee sans arguments, mesure l'ensemble complet des candidates.
    params_full = [threshold] + ([threshold] if threshold_on_both_years else []) + params
    if where:
        sql += " and " + " and ".join("g." + w for w in where)
    sql += " order by abs(g.variation_yoy_pct) desc, g.code_commune"
    if limit is not None:
        sql += " limit " + str(int(limit))
    return con.execute(sql, params_full).fetchdf()


def yoy_missing_reasons(con, annee=None, type_local=None, code_departement=None):
    """Les TROIS raisons d'un `variation_yoy_pct` NULL, tenues distinctes :
    premiere annee de la serie, predecesseur non adjacent refuse par la garde,
    volume reel sans prix observable. Les effondrer en un « n/d » perdrait la
    seule information utile des trois."""
    where, params = [], []
    if annee is not None:
        where.append("annee = ?")
        params.append(annee)
    if type_local is not None:
        where.append("type_local = ?")
        params.append(type_local)
    if code_departement is not None:
        where.append("code_departement = ?")
        params.append(code_departement)
    filter_clause = (" where " + " and ".join(where)) if where else ""

    return con.execute(
        "with series as (" + COMMUNE_SERIES + ")"
        " select"
        "   count(*) filter (where variation_yoy_pct is null and annee_prec is null)"
        "     as first_year_of_series,"
        "   count(*) filter (where variation_yoy_pct is null and annee_prec is not null"
        "                      and annee_prec <> annee - 1)"
        "     as previous_not_adjacent,"
        "   count(*) filter (where variation_yoy_pct is null and annee_prec = annee - 1)"
        "     as median_missing,"
        "   count(*) filter (where variation_yoy_pct is null) as total_missing,"
        "   count(*) as total_cells"
        " from series" + filter_clause,
        params,
    ).fetchdf().iloc[0]


def geojson_subset(geo, codes):
    """Les features des seuls departements passes en argument.

    Plotly embarque le GeoJSON dans la figure et Streamlit serialise la figure
    entiere a chaque rerun : donner les 101 features a une trace qui en dessine
    trois envoyait 3,6 Mio inutiles par clic."""
    wanted = set(codes)
    return {"type": geo["type"],
            "features": [f for f in geo["features"] if f["properties"]["code"] in wanted]}


def build_map(df_map, geo, base, annee, code_departement=None):
    """La choroplethe, fonction PURE : aucun appel a Streamlit, donc l'infobulle
    s'inspecte depuis un test. Ne dessine que la metropole ; les DOM restent
    dans les donnees. Retourne la figure et les trois listes des notes."""
    df_view = df_map[df_map["code_departement"].str.len() != 3].copy()
    dom_in_data = sorted(df_map.loc[df_map["code_departement"].str.len() == 3,
                                     "code_departement"])
    nodata_drawn = [c for c in NO_DATA if len(c) != 3]
    nodata_outside_frame = [c for c in NO_DATA if len(c) == 3]

    # La variation entre dans l'infobulle en TEXTE deja formate : sur l'annee de
    # base la valeur correcte n'est pas 0 % mais « aucune comparaison », et un
    # 0 % se lirait comme « prix inchange ».
    if annee == base:
        df_view["var_base_txt"] = "année de référence"
    else:
        df_view["var_base_txt"] = df_view["variation_vs_base_pct"].map(
            lambda v: fmt_pct(v, decimals=1, sign=True))

    # Les trois entrees numeriques passent aussi par les helpers : les formats
    # de plotly sont du d3 et ecriraient `12,592` la ou la page ecrit `12 592`.
    df_view["prix_txt"] = df_view["prix_m2_median"].map(lambda v: fmt_nb(v, unit="€/m²"))
    df_view["ventes_txt"] = df_view["n_ventes_total"].map(fmt_nb)
    df_view["base_txt"] = df_view["n_ventes_eligible"].map(fmt_nb)

    fig = px.choropleth(
        df_view, geojson=geojson_subset(geo, df_view["code_departement"]),
        locations="code_departement", featureidkey="properties.code",
        color="prix_m2_median", color_continuous_scale="Viridis",
        custom_data=["nom", "code_departement", "ventes_txt", "base_txt",
                     "var_base_txt", "prix_txt"],
        labels={"prix_m2_median": "€/m²"},
    )
    # « Base du prix median » et pas « Base des prix » : c'est le denominateur
    # de la mediane. L'annee de la variation est ecrite en toutes lettres parce
    # que le meme ecran porte deja le delta annuel du premier KPI.
    fig.update_traces(hovertemplate=(
        "<b>%{customdata[0]}</b> (%{customdata[1]})<br>"
        "Prix m² médian : %{customdata[5]}<br>"
        "Ventes (total) : %{customdata[2]}<br>"
        "Base du prix médian : %{customdata[3]}<br>"
        "Variation vs " + str(base) + " : %{customdata[4]}<extra></extra>"))

    # Contour sur le departement SELECTIONNE, sinon le clic ne montre rien. Un
    # contour et non une attenuation des autres, qui changerait la lecture des
    # couleurs. Il vit sur `marker.line` de la trace principale : une trace
    # posee par-dessus capterait les clics. Un DOM selectionne n'en a pas.
    is_selected = df_view["code_departement"] == code_departement
    fig.update_traces(
        marker_line_color=[SELECTED_COLOR if s else BORDER_COLOR for s in is_selected],
        marker_line_width=[SELECTED_WIDTH if s else BORDER_WIDTH for s in is_selected],
        # `selectOnClick` marque le polygone clique comme « selected » et plotly
        # attenuerait tous les autres, ce que le contour existe justement pour
        # eviter. Les deux etats sont donc neutralises.
        selected={"marker": {"opacity": 1}},
        unselected={"marker": {"opacity": 1}},
    )

    # Seconde couche pour les departements sans donnees : couleur pleine et
    # raison dans l'infobulle, pour ne pas les confondre avec une cellule videe
    # par un filtre.
    if nodata_drawn:
        fig.add_trace(go.Choropleth(
            geojson=geojson_subset(geo, nodata_drawn),
            locations=nodata_drawn, featureidkey="properties.code",
            z=[0] * len(nodata_drawn), showscale=False,
            colorscale=[[0, NO_DATA_COLOR], [1, NO_DATA_COLOR]],
            marker_line_color="#7a7a7a", marker_line_width=0.7,
            text=[NO_DATA[c] for c in nodata_drawn],
            hovertemplate="<b>%{location}</b><br>%{text}<extra></extra>",
        ))
    fig.update_geos(visible=False, projection_type="mercator", **BOUNDS_METROPOLE)
    # `separators` impose la convention francaise aux graduations, que plotly
    # formate cote JavaScript.
    #
    # `height=760` et colorbar HORIZONTALE : sous mercator le dessin est presque
    # carre (rapport 1,048), donc plotly le contraint a la hauteur. A 760 il
    # fait ~796 px de large et rejoint la colonne voisine. `height` est fixe,
    # donc sur une fenetre etroite du blanc revient sous la carte.
    fig.update_layout(
        margin={"r": 0, "t": 54, "l": 0, "b": 0}, height=760,
        separators="," + NBSP_THOUSANDS,
        coloraxis_colorbar={
            "orientation": "h", "x": 0.5, "xanchor": "center",
            "y": 1.0, "yanchor": "bottom",
            "thickness": 12, "len": 0.55,
            "title": {"text": "€/m²", "side": "top"},
            "ticks": "outside", "ticklen": 4,
        },
    )
    return fig, dom_in_data, nodata_drawn, nodata_outside_frame


def departement_clique(event, depts):
    """Le departement designe par un clic : (code_a_selectionner, code_sans_donnees).

    Fonction PURE, donc testable, y compris sur les cas qui ne doivent rien
    selectionner : AppTest ne sait pas simuler un clic sur un graphique. La cle
    qui porte le code ne fait pas partie du contrat public de Streamlit, donc on
    lit `properties`, `location` puis `customdata` dans cet ordre, sans deviner."""
    if not event:
        return None, None
    points = (event.get("selection") or {}).get("points") or []
    if not points:
        return None, None

    point = points[0]
    candidats = []
    properties = point.get("properties")
    if isinstance(properties, dict):
        candidats.append(properties.get("code"))
    candidats.append(point.get("location"))
    customdata = point.get("customdata") or []
    if len(customdata) > 1:
        candidats.append(customdata[1])

    for code in candidats:
        if not isinstance(code, str):
            continue
        if code in depts:
            return code, None
        if code in NO_DATA:
            return None, code
    return None, None


def excluded_share(row):
    """Part des ventes eligibles ecartees du calcul des prix.

    Denominateur = plausibles + exclues. Au-dessus de 10 % la cellule est
    signalee : sur douze ans, cinq cellules departement depassent le seuil,
    toutes antillaises. A la maille commune, 58131 n'a aucun prix utilisable."""
    base = (row["n_ventes_eligible"] or 0) + (row["n_ventes_prix_exclues"] or 0)
    return None if not base else 100.0 * row["n_ventes_prix_exclues"] / base


def style_top_n(top, annee):
    """Le tableau du top-N deja formate, fonction PURE renvoyant un Styler.

    Extraite de l'UI parce qu'AppTest ne rend que le DataFrame sous-jacent, ou
    le formatage serait invisible. `.to_html()` montre ce que voit l'utilisateur."""
    view = top[["code_commune", "nom_commune", "prix_m2_median_prec",
                 "prix_m2_median", "variation_yoy_pct",
                 "n_ventes_eligible_prec", "n_ventes_eligible"]].copy()
    view.columns = ["Code", "Commune", "Prix m² {}".format(annee - 1),
                     "Prix m² {}".format(annee), "Variation %",
                     "Base {}".format(annee - 1), "Base {}".format(annee)]
    return view.style.format({
        "Prix m² {}".format(annee - 1): fmt_nb,
        "Prix m² {}".format(annee): fmt_nb,
        "Variation %": lambda v: fmt_pct(v, sign=True),
        "Base {}".format(annee - 1): fmt_nb,
        "Base {}".format(annee): fmt_nb,
    })


# ---------------------------------------------------------------------------
# Couche Streamlit : connexion en cache_resource, requetes en cache_data.
# ---------------------------------------------------------------------------

@st.cache_resource
def get_connection():
    return connect()


@st.cache_data(show_spinner=False)
def load_geojson():
    return json.loads(GEOJSON.read_text(encoding="utf-8"))


@st.cache_data(show_spinner=False)
def q_years():
    return available_years(get_connection())


@st.cache_data(show_spinner=False)
def q_departements():
    return departments_with_data(get_connection())


@st.cache_data(show_spinner=False)
def q_kpi(annee, type_local, code_departement):
    return department_kpi(get_connection(), annee, type_local, code_departement)


@st.cache_data(show_spinner=False)
def q_base_year():
    return base_year(get_connection())


@st.cache_data(show_spinner=False)
def q_map(annee, type_local):
    return department_map(get_connection(), annee, type_local)


@st.cache_data(show_spinner=False)
def q_top_n(annee, type_local, code_departement, both, limit):
    return top_n_candidates(get_connection(), annee=annee, type_local=type_local,
                            code_departement=code_departement,
                            threshold_on_both_years=both, limit=limit)


@st.cache_data(show_spinner=False)
def q_reasons(annee, type_local, code_departement):
    return yoy_missing_reasons(get_connection(), annee, type_local, code_departement)


@st.cache_data(show_spinner=False)
def q_below_threshold():
    return dept_cells_below_threshold(get_connection())


@st.cache_data(show_spinner=False)
def department_names():
    """code -> nom, depuis le GeoJSON : couvre les 101 departements, y compris
    les 4 sans donnees, qui doivent pouvoir etre nommes a l'ecran."""
    return {f["properties"]["code"]: f["properties"]["nom"] for f in load_geojson()["features"]}


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

def choose_filters(years, depts, names):
    """Barre laterale des filtres : (annee, type_local, code_departement).

    Seul le selecteur de departement porte une cle d'etat, ecrite par le clic
    sur la carte. Valeur initiale par `setdefault` et non par `index=`, sinon
    Streamlit avertit."""
    st.sidebar.header("Filtres")
    annee = st.sidebar.selectbox("Année", years, index=len(years) - 1)
    type_local = st.sidebar.radio("Type de bien", ["Maison", "Appartement"], horizontal=True)
    st.session_state.setdefault(DEPT_KEY, "75" if "75" in depts else depts[0])
    code_departement = st.sidebar.selectbox(
        "Département", depts, key=DEPT_KEY,
        format_func=lambda c: "{} - {}".format(c, names.get(c, "?")),
        help="Cliquer un département sur la carte le sélectionne ici.",
    )
    return annee, type_local, code_departement


def kpi_section(row, annee, years):
    """Les 4 st.metric de la cellule, le delta annuel et les avertissements de
    qualite."""
    yoy = row["variation_yoy_pct"]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric(
        "Prix m² médian",
        fmt_nb(row["prix_m2_median"], unit="€/m²"),
        None if pd.isna(yoy) else "{} vs {}".format(fmt_pct(yoy, sign=True), annee - 1),
        help="Médiane du prix au m² des seules ventes plausibles. C'est l'indicateur de "
             "référence : le delta, le classement du top et la couleur de la carte "
             "reposent tous sur cette colonne.",
    )
    c2.metric("Ventes (total)", fmt_nb(row["n_ventes_total"]),
              help="Volume de marché complet : toutes les ventes valides de la cellule. "
                   "La base des prix est différente : c'est la population plausible.")
    c3.metric(
        "Prix m² moyen", fmt_nb(row["prix_m2_moyen"], unit="€/m²"),
        help="Moyenne, affichée À CÔTÉ de la médiane et non à sa place. L'écart entre les "
             "deux est une information : il mesure l'asymétrie à droite du marché, et la "
             "moyenne dépasse la médiane sur 2 251 des 2 328 cellules, jusqu'à "
             "+37,6 %. Elle n'entre pas dans le delta annuel, ne classe pas le top et ne "
             "colore pas la carte.",
    )
    c4.metric("Surface médiane", fmt_nb(row["surface_mediane"], unit="m²"),
              help="Médiane de la surface bâtie, sur la MÊME population que les mesures de "
                   "prix : elle décrit le bien typique dont prix_m2_median est le prix.")

    if pd.isna(yoy):
        # La raison se nomme au lieu de laisser un tiret. Au grain departement
        # la grille est saturee et aucune mediane n'est NULL, donc seul le
        # premier cas survient aujourd'hui ; l'autre branche reste pour le jour
        # ou la source laisserait un trou.
        if annee == years[0]:
            st.caption(
                "Pas de variation annuelle : **{} est la première année de la série** et il "
                "n'existe pas d'année précédente à comparer. Ce n'est pas une donnée "
                "manquante.".format(annee))
        else:
            st.caption(
                "Pas de variation annuelle : l'année précédente n'est pas adjacente "
                "(garde-fou sur la série) ou n'a pas de médiane utilisable. Ce n'est pas "
                "un zéro.")

    # La note sur les deux populations tient dans un depliant : longue, elle ne
    # change pas avec le filtre et repoussait la carte sous le pli.
    with st.expander("Pourquoi « Ventes (total) » et la base des prix ne donnent pas le "
                     "même nombre"):
        st.markdown(FOOTNOTE)

    # --- avertissements de qualite sur la cellule courante ---------------
    share = excluded_share(row)
    n_elig = int(row["n_ventes_eligible"])
    if share is not None and share > 10:
        st.warning(
            "**Signal de qualité** : dans cette cellule, {} des ventes structurellement "
            "éligibles ({} sur {}) sont écartées du calcul des prix, parce que leur prix "
            "au m² est symbolique ou hors d'échelle : nue-propriété, cessions "
            "intrafamiliales, surfaces improbables. La médiane décrit le reste. Le "
            "phénomène est géographiquement concentré et n'est pas du bruit : on le "
            "retrouve sur 971 Guadeloupe / Maison des deux dernières années, et à la "
            "maille commune sur 58131 (Nièvre)."
            .format(fmt_pct(share), fmt_nb(row["n_ventes_prix_exclues"]),
                    fmt_nb(row["n_ventes_eligible"] + row["n_ventes_prix_exclues"]))
        )
    if row["n_ventes_eligible"] < THRESHOLD:
        st.warning(
            "**Cellule mince** : {} ventes plausibles, sous le seuil de {} utilisé par le "
            "classement. Les valeurs restent affichées, parce qu'une cellule supprimée "
            "est indiscernable d'une donnée manquante, mais une médiane sur {} "
            "observations n'est pas un prix de marché."
            .format(int(row["n_ventes_eligible"]), THRESHOLD, int(row["n_ventes_eligible"]))
        )


def reasons_section(annee, type_local, code_departement):
    """Les trois raisons d'une variation annuelle absente, tenues distinctes."""
    m = q_reasons(annee, type_local, code_departement)
    n_celle, n_assenti = int(m["total_cells"]), int(m["total_missing"])
    with st.expander("Pourquoi tant de communes n'apparaissent pas"):
        a, b, c = st.columns(3)
        a.metric("Première année de la série", fmt_nb(m["first_year_of_series"]))
        b.metric("Année précédente non adjacente", fmt_nb(m["previous_not_adjacent"]))
        c.metric("Médiane absente", fmt_nb(m["median_missing"]))
        st.markdown(
            "Ce filtre porte sur **{}** {}. Dans **{}** cas, la variation par rapport à "
            "l'année précédente ne s'affiche pas, et chacun relève d'une seule des trois "
            "raisons ci-dessous.\n\n"
            "1. **Première année de la série.** Il n'y a pas d'année précédente à "
            "comparer.\n"
            "2. **Année précédente non adjacente.** La série a un trou, et le garde-fou "
            "refuse de comparer deux années qui ne se suivent pas. Sans lui, 26 583 lignes "
            "de la table porteraient une « variation annuelle » calculée sur des années "
            "distantes de plusieurs années, dont 26 228 avec un résultat chiffré et "
            "plausible.\n"
            "3. **Médiane absente.** La commune a des ventes, mais aucun prix exploitable. "
            "Sur toute la table, 871 cellules sont dans ce cas, pour 963 ventes "
            "réelles.\n\n"
            "Dans aucun des trois cas la variation ne s'affiche comme un zéro, qui "
            "voudrait dire « le prix n'a pas bougé » et serait faux.\n\n"
            "S'ajoutent les communes écartées par le seuil de {} ventes : elles existent, "
            "mais leur base est trop mince pour être classée. À la maille commune, "
            "**86,95 %** des cellules de la table sont dans ce cas, et le classement porte "
            "donc sur 13,05 % de la table."
            .format(fmt_nb(n_celle), plur(n_celle, "commune", "communes"),
                    fmt_nb(n_assenti), THRESHOLD)
        )


def on_map_click():
    """Callback du clic : Streamlit l'execute AVANT le corps du script, donc la
    barre laterale lit deja la nouvelle valeur. Ecrire l'etat d'un widget deja
    instancie leverait une exception."""
    code, sans_donnees = departement_clique(st.session_state.get(MAP_KEY),
                                            q_departements())
    if code:
        st.session_state[DEPT_KEY] = code
    elif sans_donnees:
        # Le message se pose ici : un st.toast appele depuis un callback
        # s'ecrirait hors de la page.
        st.session_state[CLICK_NOTICE_KEY] = sans_donnees


def map_section(annee, type_local, code_departement, names):
    """Choroplethe de la metropole, carte a gauche et notes a droite.

    Deux colonnes : a pleine largeur le dessin, presque carre, repoussait le
    top-N sous le pli. Les notes restent visibles et non dans un depliant.
    """
    st.subheader("Prix m² médian par département ({}, {})".format(type_local, annee))

    geo = load_geojson()
    df_map = q_map(annee, type_local)
    df_map["nom"] = df_map["code_departement"].map(names)
    base = q_base_year()
    fig, dom_in_data, nodata_drawn, nodata_outside_frame = build_map(
        df_map, geo, base, annee, code_departement)

    # La raison du clic sur un polygone gris arrive au moment ou la question se
    # pose, au lieu d'occuper la page en permanence.
    notice = st.session_state.pop(CLICK_NOTICE_KEY, None)
    if notice:
        st.toast("Aucune donnée pour ce département, la sélection ne change pas. {}"
                 .format(NO_DATA.get(notice, notice)))

    col_map, col_notes = st.columns(2, gap="large")
    with col_map:
        # Le clic selectionne le departement. `selection_mode="points"` est le
        # seul qui marche : geo.js n'appelle selectOnClick que si dragmode
        # n'est ni select ni lasso ET que clickmode contient select, la paire
        # que Streamlit pose pour "points" et casse pour "box". Le clic arrive
        # par plotly_selected. Barre d'outils masquee : son bouton de selection
        # rectangulaire desactiverait le clic.
        st.plotly_chart(
            fig, width="stretch", key=MAP_KEY, on_select=on_map_click,
            selection_mode="points", config={"displayModeBar": False},
        )

    with col_notes:
        # Limite declaree a l'ecran, mais en une ligne : le detail arrive par
        # le toast quand on clique sur un departement gris.
        st.caption(
            "**En gris, {} : aucune donnée.** C'est une lacune de la source, pas un "
            "filtre de ce tableau de bord. Avec Mayotte ({}), hors cadre, 97 départements "
            "sur 101 ont des données. *Cliquez un département gris pour connaître la "
            "raison.*"
            .format(", ".join(nodata_drawn), ", ".join(nodata_outside_frame))
        )
        st.caption(
            "**La carte cadre la métropole.** Les DOM ({}) sont dans les données et dans "
            "le sélecteur *Département*, mais pas sur le dessin."
            .format(", ".join(dom_in_data))
        )

        reasons_section(annee, type_local, code_departement)


def top_n_section(annee, type_local, code_departement, names):
    """Tableau du top-N : seuil sur les DEUX annees, limite arrondissement
    declaree la ou elle sert."""
    st.subheader("Top {} des communes par variation annuelle ({}, {}, {})".format(
        TOP_N, names.get(code_departement, code_departement), type_local, annee))

    # La limite se declare la ou elle sert, pas dans un depliant ferme : avec
    # 75 selectionne, rien d'autre n'explique que « commune » signifie
    # arrondissement.
    if code_departement in ARRONDISSEMENT:
        city, code_range, missing_code = ARRONDISSEMENT[code_departement]
        st.warning(
            "**Ici, « commune » signifie arrondissement.** Les DVF ne connaissent pas {} "
            "comme une commune : le code `{}` **n'existe pas dans la source**, qui livre "
            "la ville découpée en arrondissements (`{}`). Le tableau les traite tels "
            "quels, sans les agréger. À garder en tête en lisant les chiffres : les "
            "cellules sont **bien plus minces** que le nom de la ville ne le laisse "
            "croire, et beaucoup passent sous le seuil de {} ventes."
            .format(city, missing_code, code_range, THRESHOLD)
        )

    top = q_top_n(annee, type_local, code_departement, True, TOP_N)
    if top.empty:
        st.info(
            "Aucune commune de ce département n'atteint {} ventes plausibles sur les "
            "**deux** années, pour {} en {}. Il y a bien eu des ventes : simplement, "
            "aucune commune n'en a assez pour qu'une variation annuelle soit lisible."
            .format(THRESHOLD, type_local, annee)
        )
    else:
        st.dataframe(
            style_top_n(top, annee),
            width="stretch", hide_index=True,
        )
        st.caption(
            "Le classement va de la plus forte variation à la plus faible, en **valeur "
            "absolue** : une baisse de 20 % y pèse autant qu'une hausse de 20 %, et les "
            "baisses ne disparaissent donc pas sous les hausses. Pour y figurer, une "
            "commune doit compter au moins {} ventes plausibles **les deux années**, et "
            "pas seulement celle qui est affichée. C'est plus exigeant : sur l'ensemble "
            "de la table, 48 161 cellules passent, contre 55 180 si l'on ne regardait que "
            "l'année courante. Les 7 019 écartées avaient une base trop mince l'année "
            "d'avant, parfois **une seule vente** ; une variation calculée contre une "
            "médiane d'une seule vente ressemble à n'importe quel pourcentage, et n'en "
            "est pas un.".format(THRESHOLD)
        )


def limits_section():
    """Les limites connues de la vue, declarees au lieu de cachees."""
    with st.expander("Limites connues de cette vue"):
        # Calcule et pas recopie : aujourd'hui l'ensemble est vide (minimum
        # observe 84 ventes par cellule departement), et une edition future le
        # ferait reapparaitre sans toucher au code.
        below = q_below_threshold()
        if below.empty:
            st.markdown(
                "**À la maille département, aucune cellule ne passe sous le seuil de {} "
                "ventes plausibles** : les 2 328 cellules ont toutes une base suffisante, "
                "la plus mince en comptant 50. À la maille commune c'est l'inverse, et "
                "les cellules minces y sont la norme : voir le seuil du classement."
                .format(THRESHOLD)
            )
        else:
            st.markdown(
                "**Le seuil n'élimine pas un échantillon aléatoire.** À la maille "
                "département, les cellules sous {} ventes plausibles sont **{} sur 2 328** "
                "({}). C'est pourquoi les cellules minces sont ici annotées et non "
                "masquées."
                .format(THRESHOLD, fmt_nb(len(below)), fmt_pct(100.0 * len(below) / 2328))
            )
            below_view = below.rename(columns={
                "code_departement": "Département", "annee": "Année",
                "type_local": "Type de bien", "n_ventes_eligible": "Ventes (base)"})
            st.dataframe(below_view, width="stretch", hide_index=True)
        st.markdown(
            "**Paris, Lyon et Marseille n'existent pas comme communes dans les DVF.** La "
            "source livre ces trois villes découpées en arrondissements (`75101`-`75120`, "
            "`69381`-`69389`, `13201`-`13216`), et le classement les traite tels quels : "
            "45 communes séparées, aucune agrégation. Vérifié sur cette édition : les "
            "codes `75056`, `69123` et `13055` n'apparaissent dans aucune des "
            "16 565 022 mutations, alors que le référentiel COG les connaît. Agréger ne "
            "serait pas gratuit : on peut sommer des comptages, pas des médianes, parce "
            "que la médiane des médianes n'est pas la médiane. Ce que ce choix coûte : "
            "sur 991 cellules d'arrondissement, **272 (27,4 %) passent sous le seuil**, "
            "toutes des maisons, et aucune des 540 cellules `Appartement`."
        )
        st.markdown(
            "**Le seuil ne dit pas tout de l'incertitude.** Une cellule peut dépasser "
            "largement les {} ventes et porter quand même une variation fragile. Mesuré "
            "par bootstrap sur les 194 paires département × type de la comparaison "
            "2014-2025 : l'intervalle à 95 % fait 7,6 points de large en médiane, mais "
            "**14 paires dépassent 20 points**. Paris donne le contraste le plus net. Les "
            "**appartements** y gagnent +20,4 %, avec un intervalle de [+19,8 ; +21,0] "
            "large de 1,2 point sur près de 25 000 ventes. Les **maisons** gagnent "
            "+24,1 %, avec [+4,5 ; +41,8], large de 37 points sur 114 ventes. Même ville, "
            "même période, et un seul des deux chiffres est solide : c'est le nombre de "
            "ventes qui dit lequel.".format(THRESHOLD)
        )
        st.markdown(
            "**Autres limites déclarées.** Les actes qui portent sur plusieurs communes "
            "(180 390, soit 1,19 % des ventes) n'ont pas de commune attribuée : ils "
            "comptent dans les chiffres départementaux, pas dans ce tableau. Et 347 codes "
            "commune (18 629 ventes, 0,124 %) manquent au référentiel COG 2026 : des "
            "communes fusionnées depuis, dont la part diminue à mesure que l'acte est "
            "récent, plus Saint-Barthélemy et Saint-Martin, qui ne sont plus des communes. "
            "Leurs ventes comptent partout, mais sans nom lisible."
        )


def main():
    st.set_page_config(page_title="DVF - Prix de l'immobilier en France",
                       page_icon="🏠", layout="wide")

    years = q_years()
    depts = q_departements()
    names = department_names()
    annee, type_local, code_departement = choose_filters(years, depts, names)

    st.title("Prix de l'immobilier en France (DVF)")
    st.caption(
        "Maille : département × année × type de bien. Source : DVF+ open-data "
        "(Cerema, données DGFiP), années {}-{}. Les mesures de prix sont des médianes."
        .format(years[0], years[-1])
    )

    row = q_kpi(annee, type_local, code_departement)
    if row is None:
        st.error("Aucune cellule pour {} / {} / {}.".format(code_departement, type_local, annee))
        return

    kpi_section(row, annee, years)
    map_section(annee, type_local, code_departement, names)
    top_n_section(annee, type_local, code_departement, names)
    limits_section()


if __name__ == "__main__":
    main()
