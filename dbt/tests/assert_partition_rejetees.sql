-- valides + rejetees = staging entier, sans recouvrement ni trou, sur toute la
-- livraison. Prouve la partition a chaque build, et attrape une fenetre
-- d'annees reintroduite d'un seul cote. Borne aussi la part rejetee : < 2 %.

with comptes as (

    select
        (select count(*) from {{ ref('stg_dvfplus__mutations') }})          as n_staging,
        (select count(*) from {{ ref('int_dvfplus__mutations') }})          as n_valides,
        (select count(*) from {{ ref('int_dvfplus__mutations_rejected') }}) as n_rejetees

)

select
    n_staging,
    n_valides,
    n_rejetees,
    n_valides + n_rejetees      as somme_partition,
    100.0 * n_rejetees / n_staging as part_rejetees_pct,
    2.0                          as seuil_pct

from comptes

where n_valides + n_rejetees <> n_staging
   or 100.0 * n_rejetees / n_staging >= 2.0
