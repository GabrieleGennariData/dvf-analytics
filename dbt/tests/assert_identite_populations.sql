-- Dans chaque cellule des deux marts :
--     n_ventes_eligible + n_ventes_prix_exclues = lignes is_price_eligible de fct
-- C'est ce qui rend verifiable le piege de nom de n_ventes_eligible, qui
-- compte les PLAUSIBLES : compter la mauvaise population ne sort aucune valeur
-- des bornes, mais casse cette somme. Cote commune : actes a commune unique.

with ventes as (

    select * from {{ ref('fct_mutations') }}
    where type_local in ('Maison', 'Appartement')

),

fct_par_departement as (

    select
        code_departement,
        annee,
        type_local,
        count(*) filter (where is_price_eligible) as n_eligibles_fct
    from ventes
    group by 1, 2, 3

),

fct_par_commune as (

    select
        code_commune,
        annee,
        type_local,
        count(*) filter (where is_price_eligible) as n_eligibles_fct
    from ventes
    where code_commune is not null
    group by 1, 2, 3

),

ecarts_departement as (

    select
        'mart_prix_departement'                            as mart,
        m.code_departement                                 as cle,
        m.annee,
        m.type_local,
        m.n_ventes_eligible + m.n_ventes_prix_exclues      as somme_mart,
        f.n_eligibles_fct
    from {{ ref('mart_prix_departement') }} as m
    full outer join fct_par_departement as f
      on  f.code_departement = m.code_departement
      and f.annee            = m.annee
      and f.type_local       = m.type_local

),

ecarts_commune as (

    select
        'mart_prix_commune'                                as mart,
        m.code_commune                                     as cle,
        m.annee,
        m.type_local,
        m.n_ventes_eligible + m.n_ventes_prix_exclues      as somme_mart,
        f.n_eligibles_fct
    from {{ ref('mart_prix_commune') }} as m
    full outer join fct_par_commune as f
      on  f.code_commune = m.code_commune
      and f.annee        = m.annee
      and f.type_local   = m.type_local

)

select * from ecarts_departement
where somme_mart is distinct from n_eligibles_fct

union all

select * from ecarts_commune
where somme_mart is distinct from n_eligibles_fct
