-- variation_yoy_pct verifiee ligne a ligne sous une forme algebrique
-- DIFFERENTE de celle du modele : 100 * (a / b - 1) contre 100 * (a - b) / b.
-- Recopier la meme forme testerait la copie. Tolerance 1e-9 points, l'ecart
-- flottant entre les deux formes. Travaille sur deux colonnes de la meme ligne
-- grace a prix_m2_median_prec, le lag deja garde.

with comparables as (

    select
        code_commune,
        code_departement,
        type_local,
        annee,
        prix_m2_median,
        prix_m2_median_prec,
        variation_yoy_pct,
        100.0 * (prix_m2_median / prix_m2_median_prec - 1.0) as variation_recalculee
    from {{ ref('mart_prix_commune') }}
    where prix_m2_median      is not null
      and prix_m2_median_prec is not null

)

select
    code_commune,
    code_departement,
    type_local,
    annee,
    prix_m2_median,
    prix_m2_median_prec,
    variation_yoy_pct,
    variation_recalculee,
    variation_yoy_pct - variation_recalculee as ecart

from comparables

where variation_yoy_pct is null
   or abs(variation_yoy_pct - variation_recalculee) > 1e-9
