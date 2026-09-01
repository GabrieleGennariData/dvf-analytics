{{ config(materialized='table') }}

-- Complement EXACT de int_dvfplus__mutations : les mutations dont la valeur
-- fonciere n'est pas exploitable (NULL ou <= 0). Cul-de-sac declare, rien n'en
-- lit : il existe pour que les ecartees restent visibles et comptables, et
-- pour donner un numerateur au garde-fou "rejetees < 2 %".
--
-- Colonnes de diagnostic seulement, sans vocabulaire canonique ni drapeaux :
-- une mutation rejetee n'a ni prix_m2 ni population, et lui donner ces
-- colonnes inviterait a la faire entrer dans une agregation d'aval.

select
    idmutation as id_mutation,
    datemut as date_mutation,
    cast(extract(year from datemut) as integer) as annee,
    libnatmut as nature_mutation,
    vefa,
    valeurfonc as valeur_fonciere,
    nblocmut as n_locaux,
    nbpar as n_parcelles,
    codtypbien,
    libtypbien,
    coddep as code_departement,

    -- Le CASE n'a volontairement pas de branche else : le `where` est la
    -- disjonction des deux `when`, donc une regle de rejet elargie sans `when`
    -- correspondant sortirait des rejets SANS MOTIF, que not_null attrape la
    -- ou accepted_values resterait vert.
    case
        when valeurfonc is null then 'valeur_fonciere_nulle'
        when valeurfonc <= 0 then 'valeur_fonciere_non_positive'
    end as reject_reason

from {{ ref('stg_dvfplus__mutations') }}

-- Aucune borne temporelle, comme le modele frere : c'est ce qui fait que la
-- somme de leurs comptes retombe exactement sur le staging.
where valeurfonc is null or valeurfonc <= 0
