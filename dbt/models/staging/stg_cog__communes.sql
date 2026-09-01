{{ config(materialized='table') }}

-- Referentiel des noms de communes (COG Insee 2026) : DVF+ ne livre que des
-- codes. Les quatre TYPECOM sont gardes (COM, ARM, COMD, COMA) ; choisir qui
-- represente un code est le travail de dim_commune, pas du staging.
-- all_varchar : sinon les zeros de tete des codes disparaissent.

select *

from read_csv(
    '../data/raw_cog/v_commune_2026.csv',
    header = true,
    all_varchar = true
)
