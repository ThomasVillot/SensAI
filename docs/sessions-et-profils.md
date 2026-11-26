# Sessions et profils voyageur (M1)

> Toute conversation est sauvegardée automatiquement et peut être reprise plus tard ; des préférences stables (budget, rythme, allergies) sont enregistrées une fois dans un profil et respectées à chaque réponse.

## Pourquoi cette feature

- **Un voyage se prépare sur plusieurs soirées.** Sans sauvegarde, il faudrait réexpliquer chaque fois la destination, les dates et le budget. Les user stories « Sauvegarde et reprise » de M1 ([USER_STORIES.md](../USER_STORIES.md)) : sauvegarder sans rien configurer et reprendre avec `--continue` ou `--session`, y compris après avoir tué le processus.
- **Des contraintes stables ne se répètent pas.** Allergies, budget, rythme : un profil évite de les rappeler à chaque message. Le catalogue de features (Notion) cite comme références les *custom instructions* de ChatGPT et l'assistant médical qui connaît l'allergie d'un patient sans qu'on la lui répète. C'est le sujet des user stories « Profil voyageur » de M1.
- **Pourquoi deux mécanismes séparés.** L'historique est écrit par le code (ce qui a été *dit*) ; le profil est écrit par l'utilisateur et ne change pas d'un tour à l'autre. Modifier le profil change le comportement dès le lancement suivant, sans toucher au code. La mémoire que le modèle écrit lui-même est une autre feature ([mémoire du voyage](memoire-du-voyage.md)).
- **Pourquoi un fichier JSON par conversation.** D'après le code, un historique se relit en entier et dans l'ordre : il n'y a rien à interroger, et un fichier lisible à la main suffit. La base SQLite est réservée à la mémoire du voyage, où l'on additionne et où l'on filtre. Le catalogue demande de pouvoir justifier ce choix « JSON plat contre SQLite » à l'oral ; cette phrase en est la justification telle qu'elle ressort du code.
- **Le piège évité : saturer la fenêtre de contexte.** Réinjecter tout l'historique à chaque tour finit par faire oublier le début au modèle. `max_reloaded_turns` (20 par défaut) borne ce qui est renvoyé, sans rien effacer du fichier.

## Ce que ça fait

```bash
python sensai.py --continue                                  # reprend la dernière conversation
python sensai.py --session lisbonne                          # reprend ou crée la conversation « lisbonne »
python sensai.py --session lisbonne --profile budget-serre   # avec un profil voyageur
```

Rien à activer pour la sauvegarde : sans aucune option, la conversation est écrite après chaque tour et SensAI affiche où, au lancement comme à la sortie.

## Comment ça marche

### Sessions persistantes

- Après chaque tour **réussi**, la conversation (appels d'outils compris) est écrite dans `sessions/` (dossier réglé par `sessions_dir`, ignoré par git car privé). Un tour raté (erreur, `Ctrl+C`, plafond d'itérations) n'est pas sauvegardé, et une conversation sans aucun tour réussi ne crée pas de fichier.
- Sans option, le fichier porte un nom automatique : la date et l'heure, par exemple `2026-10-22_16-35-12.json`, avec un suffixe `-2`, `-3`... si ce nom existe déjà.
- `--continue` reprend la conversation modifiée le plus récemment (une neuve s'il n'y en a aucune). `--session NOM` reprend `sessions/NOM.json`, ou le crée au premier tour. Les deux sont exclusifs. Un nom n'accepte que des lettres, des chiffres, `.`, `_` et `-`.
- À la reprise : `[session] 'lisbonne' reprise : 2 tour(s) rechargé(s)`.
- `/new` (ou `/nouveau`, `/new_chat`), dans la conversation, ferme la conversation en cours et en ouvre une vierge sans relancer le programme. L'ancienne est déjà sur disque : SensAI affiche son fichier et la commande pour la reprendre (`--session NOM`), puis la nouvelle reçoit un nom automatique. Le profil voyageur reste appliqué, chaque conversation a son identifiant dans le journal, et la mémoire du voyage ([M3](memoire-du-voyage.md)) n'est pas touchée (seul [`/clear`](memoire-du-voyage.md) l'efface) : « où en est mon voyage ? » retrouve les décisions déjà prises. Sur une conversation sans aucun tour, `/new` ne fait rien et le dit.

### Format du fichier

```json
{
  "version": 1,
  "messages": [
    { "role": "user", "content": "…", "model": "qwen3:8b", "profile": "budget-serre" },
    { "role": "assistant", "content": "…", "model": "qwen3:8b", "profile": "budget-serre" }
  ]
}
```

- Les rôles sauvegardés sont `user`, `assistant` et `tool`. Le **prompt système n'est jamais écrit** : il est reconstruit à chaque lancement (config + profil). Modifier le prompt ou le profil change donc le comportement, même sur une conversation reprise.
- Chaque message porte les annotations `model` (modèle qui a joué le tour) et `profile` (profil actif à ce moment-là) ; elles sont omises quand elles n'ont pas de valeur, et les anciens fichiers sans ces champs restent lisibles. Ce sont des notes pour l'humain et l'interface : le client Ollama les retire de la requête.
- Le message `user` enregistré est celui qui a **passé les contrôles d'entrée** : données personnelles masquées (`[EMAIL]`...) et accents retirés ([garde-fous](garde-fous.md)). Un message refusé n'est jamais écrit.
- L'écriture est **atomique** (fichier temporaire, puis remplacement) : un processus tué en plein tour laisse le fichier du tour précédent intact. Si l'écriture est impossible (disque plein), la sauvegarde se désactive avec un avertissement et la conversation continue en mémoire.
- Un fichier illisible, d'une version inconnue ou dont la conversation ne commence pas par un message de l'utilisateur donne un message clair et le code de sortie 1, sans traceback.
### Fenêtre de rechargement

`max_reloaded_turns` (20 par défaut dans `config/config.json`, `null` pour tout recharger) borne le nombre de tours renvoyés au modèle. La coupure se fait toujours au début d'un tour : un message d'outil séparé de l'appel qui l'a déclenché perturberait le modèle. Les tours plus anciens restent dans le fichier et ne sont pas perdus à la sauvegarde suivante.

### Désactiver la sauvegarde

`"sessions_dir": null` dans `config/config.json` : plus rien n'est écrit, et `--session` et `--continue` sont refusés avec un message clair.

### Profil voyageur

Un profil est un objet JSON non vide dans `config/profiles/<nom>.json`, aux clés libres :

```json
{
  "voyageur": "étudiante, voyage seule",
  "budget_total_eur": 600,
  "rythme": "calme, deux activités par jour au maximum",
  "contraintes": ["allergique aux fruits de mer", "pas de vol avant 8h"],
  "instructions": "Privilégie toujours les options les moins chères. Tutoie-moi."
}
```

- Au lancement, il est ajouté à la fin du prompt système, à raison d'une ligne `- clé : valeur` par entrée (les listes sont jointes par des virgules), sous un titre qui demande au modèle de le respecter sans qu'on le lui rappelle. La commande `/prompt` affiche le prompt envoyé au modèle : c'est la preuve que le profil est injecté.
- Il reste en place si le modèle ne gère pas les outils et que SensAI bascule en chat simple.
- Il fait partie du prompt système protégé par le garde-fou de sortie : si le modèle en recopie dix mots d'affilée, sa réponse est bloquée ([garde-fous](garde-fous.md)).
- Profil inconnu (la liste des profils disponibles est affichée), illisible, vide ou au nom invalide : message clair **avant même de contacter Ollama**.
- Deux profils d'exemple sont fournis : `budget-serre` (budget 600 €, tutoiement) et `gourmet` (budget 2 500 €, vouvoiement).

### Dans l'interface web

- Les conversations sont enregistrées dans le même dossier `sessions/`, avec le même format ; l'identifiant d'une conversation est le nom de son fichier. La liste affiche le premier message et la date de dernière modification ; en ouvrir une la recharge avec la même limite `max_reloaded_turns`.
- Une réponse interrompue avec le bouton **Arrêter**, ou bloquée par un garde-fou, n'est pas conservée dans l'historique.
- Le profil se choisit dans l'en-tête et s'applique à chaque message envoyé. Le sélecteur est **verrouillé dès que la conversation contient un message** : pour changer de profil, il faut ouvrir une nouvelle conversation (l'API, elle, accepterait un profil différent à chaque message).
- Deux conversations créées dans la même seconde reçoivent des identifiants distincts (suffixe `-2`).
- Avec `sessions_dir` à `null`, les conversations n'existent qu'en mémoire du serveur.

## Limites connues

- **Rien ne résume les anciens tours.** Ceux qui sortent de la fenêtre restent dans le fichier mais ne sont plus jamais envoyés au modèle : il les « oublie ». La fenêtre se compte en tours, pas en tokens.
- **Les conversations sont des fichiers en clair** dans `sessions/`. Rien n'est chiffré.
- Deux processus qui écriraient le même fichier de session en même temps ne sont pas protégés l'un de l'autre.

## Tests

[tests/test_sessions_profiles.py](../tests/test_sessions_profiles.py) : session en mémoire, sauvegarde atomique et panne d'écriture, rechargement et fenêtre, noms de session, profils (lecture, erreurs, mise en forme), puis le comportement dans la CLI (`--session`, `--continue`, `--profile`, `/prompt`, `/new`). Les sessions sont écrites dans des dossiers temporaires.

## Fichiers

[src/session.py](../src/session.py) · [src/user_profile.py](../src/user_profile.py) · [config/profiles/](../config/profiles/) · [src/cli.py](../src/cli.py) (`open_session`) · [src/web/server.py](../src/web/server.py)
