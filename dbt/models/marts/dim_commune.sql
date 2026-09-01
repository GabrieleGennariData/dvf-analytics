{{ config(materialized='table') }}

-- dim_commune : l'unique dimension du projet. Donne un nom lisible aux codes
-- Insee, tient le cote "un" du test de relation depuis fct, et fixe en un
-- point la correspondance code_commune -> code_departement.
--
-- Source : le COG Insee 2026, PAS les donnees DVF+, qui ne livrent aucun nom.
-- ~37 000 communes contre ~33 400 touchees par une vente, donc le test de
-- relation a quelque chose a verifier au lieu d'etre vrai par construction.
--
-- 584 codes apparaissent sous plusieurs TYPECOM : priorite COM > ARM > COMD >
-- COMA, l'entite en vigueur l'emporte sur l'historique. Le row_number rend
-- l'unicite vraie par construction ; libelle et departement en queue d'order
-- by rendent l'ordre total, donc le resultat deterministe entre deux builds.
--
-- code_departement : la colonne DEP du COG fait foi quand elle est remplie
-- (COM, ARM) ; sur COMD/COMA elle est vide et le prefixe du code la remplace,
-- 3 caracteres pour '97', 2 sinon ('2A004' -> '2A').

with communes as (

    select
        "COM" as code_commune,
        "LIBELLE" as nom_commune,
        coalesce(
            nullif("DEP", ''),
            case
                when "COM" like '97%' then substr("COM", 1, 3)
                else substr("COM", 1, 2)
            end
        ) as code_departement,
        "TYPECOM" as typecom

    from {{ ref('stg_cog__communes') }}

)

select
    code_commune,
    nom_commune,
    code_departement

from communes

    -- typecom sert a choisir la ligne, pas a decrire la commune : hors sortie.
qualify row_number() over (
    partition by code_commune
    order by
        case typecom
            when 'COM' then 1
            when 'ARM' then 2
            when 'COMD' then 3
            else 4
        end,
        nom_commune,
        code_departement
) = 1
