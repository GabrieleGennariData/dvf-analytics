-- INVARIANT, tolerance zero : toute ligne is_price_plausible porte une surface
-- batie non NULL dans [9, 1500] m2. Meme statut que assert_prix_m2_range :
-- sentinelle ecrite en un point different de la definition.

select
    mutation_sk,
    id_mutation,
    surface_bati_totale,
    n_locaux,
    valeur_fonciere,
    prix_m2,
    type_local,
    code_departement

from {{ ref('int_dvfplus__mutations') }}

where is_price_plausible
  and (
        surface_bati_totale is null
     or surface_bati_totale < 9
     or surface_bati_totale > 1500
  )
