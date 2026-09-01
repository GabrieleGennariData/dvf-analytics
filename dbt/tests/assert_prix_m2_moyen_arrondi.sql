-- prix_m2_moyen est arrondie a 2 decimales dans le modele, et cet arrondi
-- conditionne la reproductibilite du CONTENU : avg() sur DOUBLE est une somme
-- parallele dont l'ordre varie (~3 ULP ; sans l'arrondi, deux runs du meme
-- binaire differaient sur 599 cellules). Le test verifie que chaque valeur est
-- un point fixe de round(x, 2).

select
    prix_dept_sk,
    code_departement,
    annee,
    type_local,
    prix_m2_moyen,
    round(prix_m2_moyen, 2) as prix_m2_moyen_attendu

from {{ ref('mart_prix_departement') }}

where prix_m2_moyen is distinct from round(prix_m2_moyen, 2)
