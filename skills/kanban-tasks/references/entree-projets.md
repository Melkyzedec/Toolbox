# Règle d'entrée d'un projet dans un Kanban multi-projets

Cette règle s'applique à un Kanban qui agrège les cartes de plusieurs projets (une catégorie par projet), par exemple un tableau de gouvernance. Elle dit quels projets alimentent le tableau, lesquels en sont exclus, et pourquoi. Elle est documentaire : la skill ne l'applique pas elle-même, c'est l'outil qui construit le tableau qui doit la respecter.

## Critères d'entrée

Un projet entre dans le Kanban si les quatre conditions sont réunies :

1. **Profil** : `produit` ou `media_business`.
2. **Statut** : `active`. Un projet en pause, archivé ou en brouillon n'entre pas.
3. **Contenu** : le projet porte au moins un élément actif (mission en cours ou à venir, idée en attente, décision à revoir). Les éléments obsolètes ou déjà traités ne comptent pas.
4. **Données sensibles** : un projet de profil `client_pme` est toujours exclu, sans exception ni dérogation.

## Liste « exclus + motif »

Chaque projet inscrit mais absent du tableau figure sur une liste d'exclus, avec un motif lisible :

| Motif | Cas |
| :--- | :--- |
| `profil_exclu` | Profil `client_pme` (toujours exclu) ou tout autre profil non prévu |
| `statut_inactif` | Statut différent de `active` |
| `aucun_element_actif` | Profil et statut valides, mais rien à afficher |

Un projet qui cumule plusieurs motifs affiche le premier de la liste ci-dessus (profil, puis statut, puis contenu).

## Structure des cartes attendue

- **Catégorie** : l'identifiant du projet.
- **Tag** : le type de carte (par exemple Mission, Idée, Décision, Tâche).
- **Titre** : le texte complet de l'élément. Le tableau l'affiche sur 2 lignes maximum ; le texte entier s'affiche au survol et dans la fenêtre de détail.

## Vérification

Contrôle simple : le nombre de projets entrés plus le nombre de projets exclus doit être égal au nombre de projets inscrits.
