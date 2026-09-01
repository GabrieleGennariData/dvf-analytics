-- Couverture de dim_commune sur les codes commune de fct, avec une tolerance
-- declaree plutot qu'un relationships strict, qui serait rouge sur un modele
-- juste : le COG 2026 ignore les communes fusionnees depuis (12 224 actes) et
-- Saint-Barthelemy / Saint-Martin, qui ne sont plus des communes (6 405).
-- Etat mesure 0,124 %, seuil a 0,30 % : au-dessus de la derive attendue, sous
-- la plus discrete des regressions (perdre les communes deleguees, ~0,55 %).

with orphelines as (

    select
        count(*)                                       as n_commune_connue,
        count(*) filter (
            where code_commune not in (select code_commune from {{ ref('dim_commune') }})
        )                                              as n_orphelines

    from {{ ref('fct_mutations') }}
    where code_commune is not null

)

select
    n_commune_connue,
    n_orphelines,
    100.0 * n_orphelines / n_commune_connue as part_orphelines_pct,
    0.30                                    as seuil_pct

from orphelines

where 100.0 * n_orphelines / n_commune_connue >= 0.30
