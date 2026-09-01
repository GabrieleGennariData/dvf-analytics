-- Tripwire : la part des eligibles ecartee par la bande de plausibilite doit
-- rester SOUS 1 %. Mesure sur 2026.1 : 37 383 sur 9 817 126, soit 0,38 %.
-- Au-dela, la bande ne trierait plus des non-observations, elle redessinerait
-- la population. Sa valeur informative depend de la bande FIXE : une regle par
-- cellule produirait une part constante et le tripwire ne mesurerait plus rien.

with populations as (

    select
        count(*) filter (where is_price_eligible)                            as n_eligibles,
        count(*) filter (where is_price_eligible and not is_price_plausible) as n_exclues

    from {{ ref('int_dvfplus__mutations') }}

)

select
    n_eligibles,
    n_exclues,
    100.0 * n_exclues / n_eligibles as quota_pct,
    1.0                             as seuil_pct

from populations

where n_eligibles = 0
   or 100.0 * n_exclues / n_eligibles >= 1.0
