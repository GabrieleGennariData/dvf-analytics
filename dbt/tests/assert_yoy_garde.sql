-- LA GARDE DU LAG, verifiee depuis l'exterieur du modele : la serie du mart
-- commune a des trous, et un lag positionnel ferait passer une variation
-- pluriannuelle pour annuelle. Le test reconstruit l'annee precedente PAR JOIN,
-- voie independante de la fenetre, et verifie les deux sens : la ou
-- (commune, type, annee - 1) existe, variation et prec doivent etre coherents ;
-- la ou elle n'existe pas, les deux doivent etre NULL.

with mart as (

    select * from {{ ref('mart_prix_commune') }}

),

avec_annee_precedente as (

    select
        m.code_commune,
        m.code_departement,
        m.type_local,
        m.annee,
        m.prix_m2_median,
        m.prix_m2_median_prec,
        m.variation_yoy_pct,
        p.prix_m2_median            as mediane_annee_precedente,
        p.code_commune is not null  as annee_precedente_existe
    from mart as m
    left join mart as p
      on  p.code_commune = m.code_commune
      and p.type_local   = m.type_local
      and p.annee        = m.annee - 1

)

select
    code_commune,
    code_departement,
    type_local,
    annee,
    prix_m2_median,
    prix_m2_median_prec,
    variation_yoy_pct,
    mediane_annee_precedente,
    annee_precedente_existe

from avec_annee_precedente

where
    -- sens 1 : le precedent existe -> prec doit etre exactement sa mediane
    (annee_precedente_existe
        and prix_m2_median_prec is distinct from mediane_annee_precedente)

    -- sens 2 : pas de precedent -> prec et variation doivent etre NULL
    or (not annee_precedente_existe
        and (prix_m2_median_prec is not null or variation_yoy_pct is not null))
