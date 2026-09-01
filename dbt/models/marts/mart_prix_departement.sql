{{ config(materialized='table') }}

-- Premier mart d'AGREGATION : il produit des mesures au lieu de les
-- transporter. Grain code_departement x annee x type_local, nouveau, donc
-- l'unicite de prix_dept_sk est etablie par le GROUP BY. Ce qui peut vraiment
-- se tromper ici, ce sont les volumes et les medianes, verifies ailleurs.
--
-- annee = annee REELLE de l'acte : agreger sur autre chose attribuerait au
-- mauvais millesime les ventes de fin d'annee.

with ventes as (

    -- Source unique : fct_mutations, PAS l'intermediate. Le filtre de nature
    -- vit la-bas ; le repeter ici en ferait une seconde copie.
    --
    -- Restriction a ('Maison', 'Appartement') : ailleurs type_local est NULL,
    -- et ces biens n'appartiennent pas a la meme distribution de prix/m2. Une
    -- cellule `type_local is null` fusionnerait des populations differentes.
    select * from {{ ref('fct_mutations') }}
    where type_local in ('Maison', 'Appartement')

)

select
    -- prix_dept_sk generee ici : le grain nait dans ce modele, la cle n'existe
    -- nulle part en amont.
    {{ dbt_utils.generate_surrogate_key(['code_departement', 'annee', 'type_local']) }} as prix_dept_sk,

    code_departement,
    annee,
    type_local,

    -- n_ventes_total : count(*) sans aucun filtre de prix. Le volume compte
    -- tout, le prix ne compte que les prix.
    count(*) as n_ventes_total,

    -- ATTENTION AU NOM : `n_ventes_eligible` compte les lignes
    -- `is_price_plausible`, la plus etroite des trois populations, et c'est
    -- elle la base des mesures de prix. Nom conserve pour la continuite avec
    -- le seuil de l'application.
    count(*) filter (where is_price_plausible) as n_ventes_eligible,

    -- n_ventes_prix_exclues : eligibles au prix juge hors marche. N'entre dans
    -- aucun calcul ; elle rend la part exclue visible dans la ligne meme, donc
    -- une cellule ou le prix symbolique est la norme se denonce toute seule.
    --
    -- IDENTITE : n_ventes_eligible + n_ventes_prix_exclues = population
    -- eligible. Elle coincide aujourd'hui avec n_ventes_total, mais c'est une
    -- coincidence des donnees et non une identite structurelle :
    -- assert_identite_populations la surveille au lieu de la supposer.
    count(*) filter (where is_price_eligible and not is_price_plausible) as n_ventes_prix_exclues,

    -- Le ratio se transporte, jamais recalcule : prix_m2 est defini dans
    -- l'intermediate, ici il s'AGREGE.
    --
    -- Le `filter (where is_price_plausible)` n'est pas redondant : sans lui les
    -- mesures tourneraient sur la population ELIGIBLE et les prix symboliques
    -- rentreraient dans les medianes, tous les tests au vert.
    median(prix_m2) filter (where is_price_plausible) as prix_m2_median,

    -- La moyenne A COTE de la mediane, pas a sa place : les queues du DVF sont
    -- longues et asymetriques, donc l'ecart entre les deux est lui-meme un
    -- indicateur. L'application classe sur la mediane.
    --
    -- ARRONDIE A 2 DECIMALES : avg() sur DOUBLE est une somme parallele dont
    -- l'ordre varie d'un run a l'autre (~3 ULP), et la reproductibilite du
    -- projet se joue sur le contenu. Seul avg() du projet.
    round(avg(prix_m2) filter (where is_price_plausible), 2) as prix_m2_moyen,

    -- surface_mediane sur la MEME population que les mesures de prix : calculee
    -- plus largement elle decrirait d'autres biens que ceux dont
    -- prix_m2_median est le prix, et inviterait a multiplier les deux.
    median(surface_bati_totale) filter (where is_price_plausible) as surface_mediane

from ventes

-- AUCUN SEUIL sur n_ventes_eligible : un seuil ici effacerait des lignes du
-- mart pour tous les consommateurs, alors qu'il ne sert qu'a la presentation.
-- Il vit dans l'application, qui s'appuie sur n_ventes_eligible. Consequence :
-- ou n_ventes_eligible = 0, les mesures sont NULL et non zero.
group by code_departement, annee, type_local
