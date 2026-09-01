{{ config(materialized='table') }}

-- fct_mutations : la frontiere entre le pipeline de construction et ce que
-- lisent les consommateurs. Les marts, les tests de population et
-- l'application partent d'ICI, jamais de l'intermediate.
--
-- Aucune mesure ajoutee : une projection avec un filtre. La seule chose
-- decidee ici est QUI compte comme vente. Grain mutation herite inchange.

with mutations as (

    -- Source unique : int_dvfplus__mutations. Le predicat de partition n'est
    -- pas repete ici : une seconde copie serait libre de diverger.
    select * from {{ ref('int_dvfplus__mutations') }}

)

select
    -- mutation_sk ARRIVE de l'intermediate : deux formules pour la meme cle
    -- peuvent diverger, et le test `unique` resterait vert sur chacune.
    mutation_sk,

    id_mutation,
    date_mutation,

    -- annee = annee REELLE de l'acte, la dimension temporelle des marts.
    annee,

    -- nature_mutation : constante 'Vente' apres le WHERE, portee quand meme
    -- pour rendre le filtre verifiable sur les donnees (accepted_values).
    nature_mutation,

    -- vefa : false par construction, porte quand meme pour que assert_vefa_exclue
    -- le PROUVE sur fct au lieu de le deduire.
    vefa,

    valeur_fonciere,
    n_locaux,
    n_parcelles,

    -- codtypbien desambigue les type_local NULL (112 "DES MAISONS", 131 "UNE
    -- DEPENDANCE", 2313 "TERRE ET PRE"...) sans retraverser la frontiere.
    codtypbien,
    libtypbien,

    -- type_local : 'Maison' (111) / 'Appartement' (121), NULL ailleurs. Cle de
    -- grain des deux marts de prix.
    type_local,

    surface_bati_totale,

    -- prix_m2 se TRANSPORTE, jamais recalcule : defini en un seul point du
    -- pipeline, qui est aussi le garde-fou de la division.
    prix_m2,

    -- Les deux populations de prix transportees comme drapeaux : aucune ligne
    -- retiree, les marts ne restreignent que les medianes.
    is_price_eligible,
    is_price_plausible,

    -- code_commune : NULL sur les multi-communes, avec un departement connu.
    code_commune,
    code_departement

from mutations

-- LE non-negociable n.1 : `= 'Vente'` EXACT, JAMAIS `like 'Vente%'`. La source
-- porte deux autres natures commencant par "Vente" : VEFA (1 099 309) et
-- terrain a batir (68 774). La VEFA est du neuf, donc un prix/m2 plus haut
-- concentre en zone urbaine : un biais sans aucune valeur hors bornes, et donc
-- invisible aux tests de range. libnatmut est scalaire, le filtre porte sur
-- une valeur et non sur un ensemble.
where nature_mutation = 'Vente'
