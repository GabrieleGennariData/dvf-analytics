{{ config(materialized='table') }}

-- Meme mouvement que mart_prix_departement, avec deux differences que ce
-- fichier existe pour gerer : la grille est CREUSE (beaucoup de cellules
-- n'existent pas, beaucoup d'autres ont du volume sans prix utilisable), et
-- c'est le premier modele qui calcule une mesure de TENDANCE, donc qui met en
-- relation des lignes differentes au lieu d'agreger dans une cellule.
--
-- Grain : code_commune x annee x type_local, etabli par le GROUP BY. annee est
-- ici la colonne qui ordonne la fenetre du lag, pas seulement celle qui
-- attribue l'acte.

with ventes as (

    -- Source unique : fct_mutations, meme frontiere que l'autre mart.
    --
    -- Restriction a ('Maison', 'Appartement') : sans elle chaque commune
    -- porterait une ligne `null` par annee et le top-N serait guide par des
    -- cellules non interpretables.
    --
    -- ET code_commune NON NULL : les actes multi-communes (~1,2 % des ventes)
    -- n'ont pas de commune, donc pas de cle. Ce mart couvre les seuls actes a
    -- commune unique, le mart departement les couvre tous, et l'ecart entre
    -- les deux totaux vaut exactement le compte des multi-communes, verifie
    -- par assert_reconciliation_marts.
    select * from {{ ref('fct_mutations') }}
    where type_local in ('Maison', 'Appartement')
      and code_commune is not null

),

celle as (

    select
        code_commune,

        -- code_departement dans le GROUP BY et pas dans un agregat : un agregat
        -- absorberait en silence une violation de la dependance commune ->
        -- departement, le GROUP BY la fait sortir en doublon de cle, donc en
        -- echec du test `unique`. C'est ce qui rend le critere falsifiable.
        code_departement,

        annee,
        type_local,

        -- n_ventes_total : count(*) sans filtre de prix.
        count(*) as n_ventes_total,

        -- ATTENTION AU NOM : compte les lignes is_price_plausible. A ce grain
        -- l'equivoque coute plus cher qu'ailleurs, elle decide de quel cote du
        -- seuil de presentation tombe une cellule.
        count(*) filter (where is_price_plausible) as n_ventes_eligible,

        -- n_ventes_prix_exclues : eligibles au prix juge hors marche. A ce grain
        -- c'est aussi ce qui rend mesurable la difference entre "commune sortie
        -- du top par le filtre plausible" et "commune sans marche".
        -- IDENTITE : n_ventes_eligible + n_ventes_prix_exclues = eligibles.
        count(*) filter (where is_price_eligible and not is_price_plausible) as n_ventes_prix_exclues,

        -- prix_m2 s'AGREGE, ne se recalcule jamais. Le filter plausible n'est
        -- pas redondant : sans lui les prix symboliques rentreraient dans les
        -- medianes en silence. Ou la cellule a du volume mais aucune ligne
        -- plausible, la mediane est NULL et non zero : c'est frequent a ce
        -- grain, et c'est la premisse de la garde sur le lag plus bas.
        median(prix_m2) filter (where is_price_plausible) as prix_m2_median

    from ventes

    -- AUCUN SEUIL sur n_ventes_eligible : a ce grain les cellules minces sont
    -- la majorite, et un mart qui les cache ne decrit plus que les villes. Le
    -- seuil est un critere de presentation et vit dans l'application.
    --
    -- AUCUNE DENSIFICATION non plus : les annees sans vente n'existent pas dans
    -- la source, et les inventer ferait apparaitre des observations que le DVF
    -- ne contient pas. Le creux se transporte, il ne se remplit pas.
    group by code_commune, code_departement, annee, type_local

),

serie as (

    -- LA DECISION CENTRALE DU MODELE : le lag sur une serie a trous.
    --
    -- `lag()` ordonne par POSITION dans la partition, pas par valeur d'annee :
    -- pour une commune ayant 2021, 2022 et 2025, le lag de 2025 est 2022, et la
    -- colonne "variation annuelle" mesurerait alors trois ans sans le dire.
    --
    -- La garde : on porte aussi `lag(annee)` et la variation ne se calcule que
    -- si l'annee precedente vaut exactement `annee - 1`. Le lag brut reste
    -- confine dans cette CTE, hors des colonnes du mart. Un frame `range`
    -- ferait la meme chose en cachant la decision dans sa semantique.
    select
        code_commune,
        code_departement,
        annee,
        type_local,
        n_ventes_total,
        n_ventes_eligible,
        n_ventes_prix_exclues,
        prix_m2_median,
        lag(annee) over (
            partition by code_commune, type_local order by annee
        ) as annee_ligne_prec,
        lag(prix_m2_median) over (
            partition by code_commune, type_local order by annee
        ) as prix_m2_median_ligne_prec
    from celle

)

select
    -- prix_commune_sk generee ici : le grain nait dans ce modele.
    -- code_departement est dans le GROUP BY mais pas dans la cle, voir `celle`.
    {{ dbt_utils.generate_surrogate_key(['code_commune', 'annee', 'type_local']) }} as prix_commune_sk,

    code_commune,
    code_departement,
    annee,
    type_local,

    n_ventes_total,
    n_ventes_eligible,
    n_ventes_prix_exclues,

    prix_m2_median,

    -- prix_m2_median_prec : le lag DEJA GARDE, expose comme colonne. Il rend la
    -- garde auditable (mediane presente et prec NULL dit que l'annee
    -- precedente manque) et fait de variation_yoy_pct une fonction de deux
    -- colonnes de la MEME ligne, donc testable sans refaire la fenetre.
    case
        when annee_ligne_prec = annee - 1 then prix_m2_median_ligne_prec
    end as prix_m2_median_prec,

    -- variation_yoy_pct en POINTS DE POURCENTAGE, pas en ratio : le nom porte
    -- l'unite parce qu'un top-N trie sur une colonne fausse d'un facteur 100
    -- reste trie pareil, et le bug ne se verrait que dans les etiquettes.
    --
    -- Pas de garde sur la division : une mediane non NULL est >= 100 par
    -- construction. NULL pour trois raisons a garder distinctes cote
    -- application : premiere ligne, trou dans la serie, mediane absente d'un
    -- cote. Aucune n'est un zero.
    case
        when annee_ligne_prec = annee - 1
            then 100.0 * (prix_m2_median - prix_m2_median_ligne_prec) / prix_m2_median_ligne_prec
    end as variation_yoy_pct

from serie
