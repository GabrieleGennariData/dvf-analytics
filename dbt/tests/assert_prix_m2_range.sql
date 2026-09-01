-- INVARIANT, tolerance zero : toute ligne is_price_plausible porte un prix_m2
-- non NULL dans [100, 40 000] EUR/m2. Quasi tautologique, et c'est le but :
-- les bornes sont ecrites ici en SENTINELLE et dans l'intermediate en
-- DEFINITION, donc une divergence entre les deux doit frapper deux fois.

select
    mutation_sk,
    id_mutation,
    prix_m2,
    valeur_fonciere,
    surface_bati_totale,
    type_local,
    code_departement

from {{ ref('int_dvfplus__mutations') }}

where is_price_plausible
  and (
        prix_m2 is null
     or prix_m2 < 100
     or prix_m2 > 40000
  )
