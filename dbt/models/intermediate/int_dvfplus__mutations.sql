{{ config(materialized='table') }}

-- Couche semantique : les colonnes DVF+ deviennent le vocabulaire canonique du
-- pipeline, et les trois definitions qui gouvernent l'aval vivent ici (vente
-- ordinaire, eligibilite au prix/m2, observation de marche). Les drapeaux ne
-- retirent aucune ligne : le volume compte tout, le prix ne compte que les prix.
--
-- Grain mutation deja fourni par la source (idmutation unique sur les
-- 16 565 022 lignes), donc aucune deduplication. Aucun filtre d'annee : une
-- annee livree en plus entrera sans qu'une constante bouge.

with mutations as (

    select * from {{ ref('stg_dvfplus__mutations') }}

)

select
    -- Cle de substitution au grain mutation. idmutinvar n'entre pas dans la
    -- cle : deux definitions candidates sont une divergence en attente.
    {{ dbt_utils.generate_surrogate_key(['idmutation']) }} as mutation_sk,

    idmutation as id_mutation,
    datemut as date_mutation,

    -- annee = annee REELLE de l'acte, derivee de la date en un seul point.
    cast(extract(year from datemut) as integer) as annee,

    libnatmut as nature_mutation,

    -- vefa : drapeau de la source, PORTE et non utilise dans les definitions.
    -- La VEFA est deja exclue par nature_mutation = 'Vente' ; le drapeau reste
    -- comme verification independante (assert_vefa_exclue).
    vefa,

    valeurfonc as valeur_fonciere,
    nblocmut as n_locaux,
    nbpar as n_parcelles,

    -- Typologie officielle du bien, portee telle quelle : 111 = UNE MAISON,
    -- 121 = UN APPARTEMENT, 112/122 = pluriel, 110/120 = indetermine. Jamais NULL.
    codtypbien,
    libtypbien,

    -- type_local : vocabulaire canonique du projet, donne aux seules classes
    -- mono-bien. Decision de PERIMETRE : une valeur fonciere qui couvre
    -- plusieurs biens ne dit le prix au m2 d'aucun.
    case codtypbien
        when '111' then 'Maison'
        when '121' then 'Appartement'
    end as type_local,

    -- Surface batie totale, fournie par la source. Sur les classes 111/121 elle
    -- vaut la surface du type (sbatmai / sbatapt) : 15 lignes d'ecart sur
    -- 5,6 millions, toutes en 2014-2016. Les dependances n'y sont pas comptees.
    sbati as surface_bati_totale,

    -- Eligibilite au prix/m2 : vente ordinaire, bien mono-logement de la
    -- typologie officielle, surface positive. 9 817 126 mutations. Le
    -- mono-logement vient de codtypbien et non d'un comptage de locaux, donc
    -- une maison vendue avec sa dependance reste dans le perimetre.
    -- `= 'Vente'` EXACT : un like ferait entrer la VEFA (1 099 309) et les
    -- terrains a batir (68 774).
    --
    -- A savoir avant de lire `n_locaux` sur une serie longue : la part des 111
    -- avec dependance passe de ~5 % (2014-2019) a 30-37 % (2021-2025), un
    -- changement d'enregistrement de l'editeur. Il ne contamine pas les
    -- mesures : sbati reste la surface du logement seul et la mediane
    -- nationale ne montre aucune rupture au passage.
    --
    -- COALESCE a false : la garantie BOOLEAN non-NULL vient de la construction,
    -- pas des donnees du jour.
    coalesce(
        libnatmut = 'Vente'
        and codtypbien in ('111', '121')
        and sbati > 0,
        false
    ) as is_price_eligible,

    -- Le prix au m2 se calcule ICI, une seule fois ; l'aval l'agrege sans
    -- jamais refaire la division. Le CASE est aussi le garde-fou : sur DOUBLE
    -- x/0 donne `inf`, ni erreur ni NULL, et la division n'est evaluee que la
    -- ou is_price_eligible est vrai. Alias lateral verifie en execution sur
    -- Fusion ; repeter le predicat creerait la divergence qu'on evite ici.
    case
        when is_price_eligible
        then valeurfonc / sbati
    end as prix_m2,

    -- Observation de MARCHE : eligible ET prix/m2 dans [100, 40 000] EUR/m2 ET
    -- surface dans [9, 1500] m2. La bande DEFINIT la population de prix, elle
    -- ne filtre pas des outliers : l'editeur ne filtre pas les prix, et aucun
    -- seuil ne fait d'une cession a 1 euro une observation de marche. Memes
    -- bornes que assert_prix_m2_range et assert_surface_range : ici la
    -- definition, la-bas la sentinelle. COALESCE a false pour la meme raison
    -- que plus haut.
    coalesce(
        is_price_eligible
        and prix_m2 between 100 and 40000
        and sbati between 9 and 1500,
        false
    ) as is_price_plausible,

    -- code_commune : la source livre une LISTE de communes, un acte pouvant en
    -- toucher plusieurs. La commune n'est definie que si elle est UNIQUE
    -- (98,81 % des ventes ; 180 390 restent NULL). Jamais un premier element
    -- de liste : cela fausserait le grain commune en silence.
    case
        when nbcomm = 1 then l_codinsee
    end as code_commune,

    coddep as code_departement

from mutations

-- REGLE DE PARTITION, complement exact de int_dvfplus__mutations_rejected :
-- une mutation sans valeur fonciere exploitable (NULL ou <= 0) ne porte aucune
-- mesure du projet. 37 417 sur 16 565 022, soit 0,226 %. Le predicat ne
-- produit jamais NULL, donc la partition est exacte.
where valeurfonc is not null
  and valeurfonc > 0
