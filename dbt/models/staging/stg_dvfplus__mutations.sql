{{ config(materialized='table') }}

-- Lecture des 97 CSV departementaux DVF+ (Cerema, edition 2026.1 / ED251) en
-- une passe : separateur "|", en-tete identique sur les 97 fichiers. Aucun
-- filtre, aucune agregation : la source est deja au grain mutation.
--
-- types= la ou l'inference se tromperait : coddep VARCHAR (sinon "2A"/"2B"
-- casse la lecture et "01" devient 1), colonnes-listes l_* VARCHAR,
-- codtypbien VARCHAR (code hierarchique), identifiants VARCHAR, et types
-- explicites sur les colonnes critiques (datemut, valeurfonc, vefa, surfaces).

select *

from read_csv(
    '../data/raw_dvfplus/1_DONNEES_LIVRAISON/dvf_plus_d*.csv',
    delim = '|',
    header = true,
    sample_size = 200000,
    types = {
        'idmutation': 'VARCHAR',
        'idmutinvar': 'VARCHAR',
        'idopendata': 'VARCHAR',
        'refdoc': 'VARCHAR',
        'datemut': 'DATE',
        'anneemut': 'INTEGER',
        'moismut': 'INTEGER',
        'coddep': 'VARCHAR',
        'libnatmut': 'VARCHAR',
        'l_artcgi': 'VARCHAR',
        'vefa': 'BOOLEAN',
        'valeurfonc': 'DOUBLE',
        'nbcomm': 'INTEGER',
        'l_codinsee': 'VARCHAR',
        'l_section': 'VARCHAR',
        'nbpar': 'INTEGER',
        'l_idpar': 'VARCHAR',
        'l_idparmut': 'VARCHAR',
        'sterr': 'DOUBLE',
        'l_dcnt': 'VARCHAR',
        'nblocmut': 'INTEGER',
        'l_idlocmut': 'VARCHAR',
        'nblocmai': 'INTEGER',
        'nblocapt': 'INTEGER',
        'nblocdep': 'INTEGER',
        'nblocact': 'INTEGER',
        'sbati': 'DOUBLE',
        'sbatmai': 'DOUBLE',
        'sbatapt': 'DOUBLE',
        'sbatact': 'DOUBLE',
        'codtypbien': 'VARCHAR',
        'libtypbien': 'VARCHAR'
    }
)
