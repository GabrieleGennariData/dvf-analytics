-- Trois verifications independantes des volumes contre fct_mutations :
-- 1. chaque cellule du mart departement porte le n_ventes_total recompte
--    depuis fct (full outer join : cellule manquante ou en trop = faute) ;
-- 2. idem pour le mart commune, sur les actes a commune unique ;
-- 3. l'ecart entre les totaux des deux marts vaut exactement le compte des
--    actes multi-communes : une IDENTITE, pas un residu tolere.

with ventes as (

    select * from {{ ref('fct_mutations') }}
    where type_local in ('Maison', 'Appartement')

),

fct_par_departement as (

    select code_departement, annee, type_local, count(*) as n_ventes_fct
    from ventes
    group by 1, 2, 3

),

fct_par_commune as (

    select code_commune, annee, type_local, count(*) as n_ventes_fct
    from ventes
    where code_commune is not null
    group by 1, 2, 3

),

ecarts_departement as (

    select
        'cellule departement'          as verification,
        coalesce(m.code_departement, f.code_departement) as cle,
        coalesce(m.annee, f.annee)     as annee,
        m.n_ventes_total,
        f.n_ventes_fct
    from {{ ref('mart_prix_departement') }} as m
    full outer join fct_par_departement as f
      on  f.code_departement = m.code_departement
      and f.annee            = m.annee
      and f.type_local       = m.type_local
    where m.n_ventes_total is distinct from f.n_ventes_fct

),

ecarts_commune as (

    select
        'cellule commune'              as verification,
        coalesce(m.code_commune, f.code_commune) as cle,
        coalesce(m.annee, f.annee)     as annee,
        m.n_ventes_total,
        f.n_ventes_fct
    from {{ ref('mart_prix_commune') }} as m
    full outer join fct_par_commune as f
      on  f.code_commune = m.code_commune
      and f.annee        = m.annee
      and f.type_local   = m.type_local
    where m.n_ventes_total is distinct from f.n_ventes_fct

),

ecart_totaux as (

    select
        'totaux dept - commune = multi-communes' as verification,
        cast(null as varchar)          as cle,
        cast(null as integer)          as annee,
        (select sum(n_ventes_total) from {{ ref('mart_prix_departement') }})
          - (select sum(n_ventes_total) from {{ ref('mart_prix_commune') }})
                                       as n_ventes_total,
        (select count(*) from ventes where code_commune is null)
                                       as n_ventes_fct
    -- l'ecart des totaux DOIT valoir le compte des multi-communes
    where (select sum(n_ventes_total) from {{ ref('mart_prix_departement') }})
        - (select sum(n_ventes_total) from {{ ref('mart_prix_commune') }})
       <> (select count(*) from ventes where code_commune is null)

)

select * from ecarts_departement
union all
select * from ecarts_commune
union all
select * from ecart_totaux
