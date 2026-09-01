-- La VEFA (du neuf, prix/m2 systematiquement plus haut) est exclue de fct par
-- `nature_mutation = 'Vente'`. Ce test le prouve par une colonne INDEPENDANTE,
-- le drapeau `vefa` de la source : 1 099 309 des deux cotes, zero croisement.
-- Non-negociable n.1 rendu prouvable la ou un `like 'Vente%'` ne sortirait
-- aucune valeur des bornes.

select
    mutation_sk,
    id_mutation,
    annee,
    nature_mutation,
    vefa,
    code_departement

from {{ ref('fct_mutations') }}

where vefa
