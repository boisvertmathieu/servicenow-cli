---
name: servicenow-itsm
description: >-
  Interroger ServiceNow en lecture avec le CLI `sn` (servicenow-cli) pour les
  incidents (INC, table incident), les changements (CHG, change_request), les
  problèmes (PRB, problem) et les articles de la base de connaissances (KB,
  kb_knowledge). À utiliser dès qu'une demande mentionne un numéro INC/CHG/PRB/KB,
  ServiceNow, un ticket, une panne, une file d'incidents, un changement planifié,
  un CAB, une cause racine, une erreur connue (known error), un contournement ou un
  article KB : retrouver une fiche, lire son historique (work notes), chercher des
  incidents similaires, relier incidents, problèmes et changements, compter ou
  regrouper des tickets, ou trouver la procédure documentée. Use for ServiceNow
  incident / change / problem / knowledge lookups via the `sn` command.
---

# ServiceNow ITSM avec `sn` (INC, CHG, PRB, KB)

`sn` interroge l'instance ServiceNow de l'utilisateur **avec ses droits** (mêmes
ACL que l'interface). Ce skill couvre quatre tables :

| Préfixe | Table | Rôle | Détails |
|---|---|---|---|
| `INC` | `incident` | Interruption ou dégradation de service | [references/incident.md](references/incident.md) |
| `CHG` | `change_request` (+ `change_task`) | Modification planifiée | [references/change.md](references/change.md) |
| `PRB` | `problem` (+ `problem_task`) | Cause sous-jacente d'incidents | [references/problem.md](references/problem.md) |
| `KB` | `kb_knowledge` | Article de connaissance | [references/knowledge.md](references/knowledge.md) |

Lisez le fichier de référence de la table concernée **avant** d'écrire une requête
non triviale : il donne les champs, les valeurs stockées et les relations. Les
enchaînements d'analyse multi-tables sont dans
[references/workflows.md](references/workflows.md). La référence complète du CLI
est dans `docs/reference.md` à la racine du dépôt.

## 1. Avant de commencer

```bash
command -v sn || echo "sn absent"   # dans ce dépôt : `uv run sn ...`
sn whoami --format json              # vérifie la session (une seule fois par tâche)
```

* `sn whoami` échoue (code 1, « not signed in », « session expired »...) :
  **arrêtez-vous** et demandez à l'utilisateur de lancer `sn login` lui-même. La
  connexion passe par un SSO avec MFA dans une fenêtre de navigateur ; un agent ne
  peut pas la faire. Ne tentez pas `sn login -m cookie` avec des valeurs devinées.
* Aucune instance configurée (code 2) : demandez le nom de l'instance, puis
  `sn configure --instance <nom>`.

## 2. Règles d'efficacité

1. **Toujours `--format json`** pour lire un résultat. Les tableaux sont faits pour
   un humain : colonnes tronquées, titres sur stderr. Le JSON contient toujours
   `sys_id` et `_url` (lien vers la fiche).
2. **Toujours restreindre les champs avec `-f`** et le volume avec `-n`. Une fiche
   complète (`sn get` sans `-f`) fait plusieurs centaines de lignes ; ne la
   demandez que si vous en avez vraiment besoin.
3. **Compter avec l'API Aggregate**, pas en téléchargeant les fiches :
   `sn api /api/now/stats/incident -p sysparm_count=true -p "sysparm_query=..."`.
4. **Requêtes entre apostrophes** : `-q 'active=true^priority<=2'` (`<`, `>`, `^`,
   espaces et `$` sont sinon interprétés par bash).
5. **Valeurs stockées, pas libellés** dans `-q` : `state=2`, pas
   `state=In Progress`. Pour les références, dot-walkez vers un champ lisible :
   `assignment_group.name=Network`, `cmdb_ci.name=SRV-APP-01`. Les sorties, elles,
   sont des libellés (sauf `sn query --raw-values`).
6. **Un champ mal orthographié est ignoré silencieusement** et la requête renvoie
   toute la table (jusqu'à `-n`). Si un résultat paraît trop large, vérifiez le nom
   du champ avec `sn get <table> <numéro> --format json`.
7. Les valeurs de choix (`state`, `close_code`, `category`...) peuvent être
   personnalisées. En cas de doute :
   `sn query sys_choice -q 'name=incident^element=state^inactive=false' -f value,label,language -n 50 --format json`.
8. Lancez en parallèle les commandes indépendantes (ex. fiche + journal + fiches
   liées).
9. `jq` n'est pas forcément installé. Repli :
   `python3 -c 'import json,sys; d=json.load(sys.stdin); ...'`.

## 3. Choisir la commande

| Besoin | Commande |
|---|---|
| Fiche connue par son numéro | `sn get INC0012345 -f <champs> --format json` |
| Texte libre, table inconnue | `sn search "<mots>" -t incident,problem,change_request,kb_knowledge -n 5 --format json` |
| Texte libre + filtre | `sn search "<mots>" -t incident -q 'active=true' --format json` |
| Filtre structuré, sans mots-clés | `sn query incident -q '<requête encodée>' -f <champs> -o -sys_updated_on --format json` |
| Article KB pour un symptôme | `sn kb "<symptôme>" -n 5 --format json` |
| Compter / regrouper | `sn api /api/now/stats/<table> -p sysparm_count=true -p sysparm_group_by=<champ> -p "sysparm_query=..."` |
| Historique (work notes, commentaires) | `sn get <NUM> -f work_notes,comments --format json` |
| Lien à donner à l'utilisateur | champ `_url` du JSON (n'utilisez pas `sn open`, qui lance un navigateur) |

Notes :

* `sn search` cherche par mots-clés (index texte, tri par pertinence) avec repli
  automatique en `LIKE` ; `mode` vaut alors `like` dans le JSON. Sa sortie JSON est
  une liste `[{"table", "rows", "error", "mode"}]`. Une table en erreur n'arrête
  pas les autres : lisez `error`.
* `sn query`, `sn kb` renvoient une liste de lignes ; `sn get` un objet.
* Ne mettez **jamais** `^NQ` dans le `-q` de `sn search` (il crée un bloc sans la
  condition de recherche). Pour une logique OU complexe, utilisez `sn query`.
* `sn get` avec un seul argument déduit la table du préfixe (`INC`, `CHG`, `CTASK`,
  `PRB`, `PTASK`, `KB`...). Si l'instance utilise d'autres préfixes, passez la
  table : `sn get change_request CHG0030001`.

## 4. Valeurs par défaut des états (à vérifier sur l'instance)

| Table.champ | Valeurs stockées |
|---|---|
| `incident.state` | `1` New, `2` In Progress, `3` On Hold, `6` Resolved, `7` Closed, `8` Canceled |
| `change_request.state` | `-5` New, `-4` Assess, `-3` Authorize, `-2` Scheduled, `-1` Implement, `0` Review, `3` Closed, `4` Canceled |
| `change_request.type` | `normal`, `standard`, `emergency` |
| `problem.state` | `101` New, `102` Assess, `103` Root Cause Analysis, `104` Fix in Progress, `106` Resolved, `107` Closed |
| `kb_knowledge.workflow_state` | `draft`, `review`, `published`, `pending_retirement`, `retired`, `outdated` |
| `priority` | `1` Critical, `2` High, `3` Moderate, `4` Low, `5` Planning |
| `impact`, `urgency` | `1` High, `2` Medium, `3` Low |

`active=true` signifie « pas encore fermé » sur toutes ces tâches (sauf KB).

## 5. Dates et utilisateur courant

Préférez les fonctions évaluées par l'instance aux dates littérales (problèmes de
fuseau horaire) :

```text
assigned_to=javascript:gs.getUserID()                 # moi
assignment_group=javascript:getMyGroups()             # mes groupes
opened_at>=javascript:gs.daysAgoStart(7)              # 7 derniers jours
sys_updated_onRELATIVEGT@hour@ago@24                  # modifié dans les 24 h
start_date>=javascript:gs.beginningOfToday()          # à partir d'aujourd'hui
resolved_atBETWEENjavascript:gs.beginningOfLastMonth()@javascript:gs.endOfLastMonth()
```

## 6. Sécurité : lecture seule par défaut

* `sn get`, `search`, `query`, `kb`, `whoami`, `status` et `sn api` en `GET` sont
  en lecture seule. Utilisez-les librement.
* `sn api -X POST|PATCH|PUT|DELETE` **modifie réellement** l'instance (ajout de
  work note, changement d'état, assignation...). Ne le faites que si l'utilisateur
  le demande explicitement, montrez-lui la commande exacte et attendez sa
  confirmation. Ne contournez jamais un refus d'ACL (403).
* Les fiches peuvent contenir des données personnelles (appelants, adresses,
  journaux). N'en recopiez que ce qui sert la demande et ne les envoyez pas à un
  service externe.
* Ne lisez pas et n'affichez pas les identifiants stockés
  (`~/.local/state/sncli/credentials/`).

## 7. Restituer le résultat

* Citez chaque fiche par son **numéro** et donnez son `_url` quand l'utilisateur
  voudra l'ouvrir.
* Résumez les journaux (work notes) au lieu de les recopier ; datez les étapes
  clés (ouverture, escalade, résolution).
* Signalez les limites : résultats tronqués par `-n`, repli `LIKE`, table en
  erreur, valeurs d'état supposées par défaut.
