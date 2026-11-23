# Référence des commandes `sn`

Ce document liste toutes les commandes et toutes les options de `sn`, avec leur
comportement exact (valeurs par défaut, interactions entre options, formats de
sortie). La section [Requêtes encodées (`-q`)](#requêtes-encodées--q) explique ce
qu'on peut passer à `-q` / `--query`.

Pour l'installation et la connexion, voir le [README](../README.md).

## Sommaire

* [Options globales](#options-globales)
* [Lecture et recherche](#lecture-et-recherche)
  * [`sn search`](#sn-search) : recherche par mots-clés sur plusieurs tables
  * [`sn query`](#sn-query) : requête encodée sur une table
  * [`sn get`](#sn-get) : afficher une fiche
  * [`sn kb`](#sn-kb) : rechercher dans la base de connaissances
  * [`sn ci-link`](#sn-ci-link) : une offre de service est-elle liée à un CI ?
  * [`sn open`](#sn-open) : ouvrir une fiche dans le navigateur
  * [`sn api`](#sn-api) : appeler n'importe quel endpoint REST
* [Requêtes encodées (`-q`)](#requêtes-encodées--q)
* [Configuration et session](#configuration-et-session) :
  [`configure`](#sn-configure), [`login`](#sn-login), [`logout`](#sn-logout),
  [`whoami`](#sn-whoami), [`status`](#sn-status)
* [Formats de sortie](#formats-de-sortie)
* [Variables d'environnement](#variables-denvironnement)
* [Codes de sortie](#codes-de-sortie)

---

## Options globales

Elles se placent **avant** le nom de la commande : `sn -P prod search vpn`.

| Option | Description |
|---|---|
| `-P`, `--profile NOM` | Profil de configuration à utiliser. Défaut : la variable `SN_PROFILE`, sinon le profil par défaut du fichier de configuration, sinon `default`. |
| `--version` | Affiche la version et quitte. |
| `-h`, `--help` | Aide. Fonctionne aussi après chaque commande : `sn search -h`. |

---

## Lecture et recherche

Règles communes :

* Les options qui acceptent plusieurs valeurs (`-t`, `-f`) peuvent être
  **répétées** ou recevoir une **liste séparée par des virgules** :
  `-t incident -t problem` équivaut à `-t incident,problem`.
* Le champ `sys_id` est toujours demandé (il sert à construire les liens), mais il
  n'est pas affiché dans les tableaux ni dans le CSV.
* Les valeurs renvoyées sont les **valeurs d'affichage** (`In Progress`,
  `1 - Critical`, nom du groupe...), sauf avec `sn query --raw-values`.
* Les noms de champs se trouvent dans ServiceNow : clic droit sur l'en-tête d'un
  formulaire, *Configure > Dictionary*, ou `sn get <table> <id> --format json`.

### `sn search`

```text
sn search [OPTIONS] TEXTE...
```

Recherche par mots-clés dans plusieurs tables à la fois (en parallèle, 6 tables
au maximum en même temps). Les mots sont joints par des espaces :
`sn search vpn montreal` cherche `vpn montreal`.

| Option | Défaut | Description |
|---|---|---|
| `-t`, `--table TABLE` | tables du profil | Table(s) à interroger. Répétable ou séparé par des virgules. Sans `-t` : les tables par défaut du profil (`incident,sc_req_item,change_request,problem,kb_knowledge`, modifiables avec `sn configure --tables`). |
| `-n`, `--limit N` | `10` | Nombre maximal de résultats **par table**. |
| `-q`, `--query REQUÊTE` | aucune | Filtre supplémentaire en [requête encodée](#requêtes-encodées--q), ajouté avec un ET (`^`) à la condition de recherche. |
| `-f`, `--fields CHAMPS` | préréglage par table | Champs à afficher. **Remplace** le préréglage (voir [Champs par défaut](#champs-par-défaut)). Le dot-walking est accepté : `-f number,assignment_group.name`. |
| `--like` | désactivé | Force une recherche `LIKE` sur `short_description` OU `description` au lieu de la recherche par mots-clés. |
| `--url` | désactivé | Ajoute une colonne `url` (lien vers chaque fiche). |
| `--format table\|json\|csv` | `table` | Voir [Formats de sortie](#formats-de-sortie). |

**Fonctionnement de la recherche.** Pour chaque table, `sn` envoie la requête
`123TEXTQUERY321=<texte>` : c'est la recherche par mots-clés de ServiceNow (index
texte, la même que la barre de recherche de l'interface). Les résultats sont donc
triés par pertinence, et la syntaxe disponible dans le texte (guillemets pour une
expression exacte, `*` en fin de mot, etc.) dépend de la configuration de
recherche de votre instance.

**Repli automatique sur `LIKE`.** Si l'instance refuse la recherche par
mots-clés sur une table (HTTP 400, 403 ou 500, typiquement une table non indexée),
`sn` relance la requête en `LIKE` :

* sur `short_description` OU `description` si `short_description` fait partie des
  champs demandés ;
* sinon sur `name` (utile pour `sys_user`, `sys_user_group`, `cmdb_ci`...).

Le titre de la table affiche alors `LIKE` : `incident (3, LIKE)`. En JSON, le
champ `mode` vaut `like`.

> Le `LIKE` cherche la chaîne entière : `sn search --like "vpn lent"` ne trouve
> que les fiches qui contiennent exactement `vpn lent`. La recherche par mots-clés,
> elle, accepte les mots dans n'importe quel ordre.

**Erreurs par table.** Une table en erreur (inexistante, droits insuffisants)
n'interrompt pas les autres : un avertissement jaune est écrit sur la sortie
d'erreur et la commande se termine normalement.

**Caractères particuliers.** Le caractère `^` est retiré du texte (il sépare les
conditions d'une requête encodée et ne peut pas être échappé). Pour chercher un
texte qui commence par `-`, séparez-le des options avec `--` :
`sn search -- -5000`.

**Exemples**

```bash
sn search vpn
sn search "imprimante 3e étage" -t incident -n 25
sn search vpn -t incident,problem -q "active=true^priority<=2"
sn search vpn -q "assignment_group.name=Network^ORDERBYDESCsys_updated_on"
sn search jdoe -t sys_user -f user_name,name,email,department
sn search "contrat" -t sc_req_item,sc_task --url
sn search "mot de passe" --like -t incident
sn search vpn --format json | jq '.[] | select(.table=="incident") | .rows[].number'
```

`sn search` exige un texte. Pour filtrer sans mots-clés, utilisez `sn query`.

### `sn query`

```text
sn query [OPTIONS] TABLE
```

Exécute une [requête encodée](#requêtes-encodées--q) sur une table quelconque.
C'est l'équivalent d'une vue liste filtrée dans ServiceNow.

| Option | Défaut | Description |
|---|---|---|
| `-q`, `--query REQUÊTE` | vide (toute la table) | Requête encodée complète. Se copie depuis une liste ServiceNow : clic droit sur le fil d'Ariane du filtre > *Copy query*. |
| `-f`, `--fields CHAMPS` | préréglage par table | Champs à afficher (remplace le préréglage). Dot-walking accepté. |
| `-n`, `--limit N` | `20` | Nombre maximal de lignes. Au-delà de 100, `sn` pagine automatiquement. |
| `-o`, `--order-by CHAMP` | aucun | Tri. Préfixe `-` pour un tri décroissant : `-o -sys_updated_on`. Ajoute `ORDERBY<champ>` ou `ORDERBYDESC<champ>` à la fin de la requête. |
| `--raw-values` | désactivé | Renvoie les valeurs brutes (valeur stockée de la liste de choix, `sys_id` des références, dates en UTC) au lieu des valeurs d'affichage. Pratique pour connaître la valeur à mettre dans `-q`. |
| `--url` | désactivé | Ajoute une colonne `url`. |
| `--format table\|json\|csv` | `table` | Voir [Formats de sortie](#formats-de-sortie). |

Sans `-o` ni `ORDERBY` dans la requête, l'ordre des lignes n'est pas garanti.

**Exemples**

```bash
sn query incident -q "active=true^assigned_to=javascript:gs.getUserID()"
sn query incident -q "assignment_group.name=Service Desk^active=true" -o -sys_updated_on
sn query sc_req_item -q "requested_for.user_name=jdoe^active=true" --url
sn query change_request -q "start_date>=javascript:gs.beginningOfToday()" -o start_date
sn query sys_user_group -q "nameLIKEreseau" -f name,manager,email
sn query incident -q "priority=1^active=true" -n 500 --format csv > p1.csv
sn query incident -q "number=INC0012345" --raw-values --format json
```

### `sn get`

```text
sn get [OPTIONS] NUMÉRO
sn get [OPTIONS] TABLE NUMÉRO|SYS_ID
```

Affiche une fiche complète (tous les champs non vides), sous forme
clé / valeur.

* Avec un seul argument, la table est déduite du préfixe du numéro (insensible à
  la casse) :

  | Préfixe | Table | Préfixe | Table |
  |---|---|---|---|
  | `INC` | `incident` | `CHG` | `change_request` |
  | `RITM` | `sc_req_item` | `CTASK` | `change_task` |
  | `REQ` | `sc_request` | `PRB` | `problem` |
  | `SCTASK` | `sc_task` | `PTASK` | `problem_task` |
  | `TASK` | `task` | `KB` | `kb_knowledge` |

  Ce sont les préfixes par défaut de ServiceNow. Si votre instance en utilise
  d'autres, ou pour une autre table, passez la table explicitement.
* Un `sys_id` (32 caractères hexadécimaux en minuscules) exige toujours la table :
  `sn get change_request 3f2c...`.

| Option | Défaut | Description |
|---|---|---|
| `-f`, `--fields CHAMPS` | tous | N'affiche que ces champs (plus `sys_id` et `url`). |
| `--format table\|json\|csv` | `table` | `json` donne l'objet JSON. `csv` n'est pas géré pour une fiche : la sortie est un tableau. |

```bash
sn get INC0012345
sn get inc0012345 -f number,state,assigned_to,work_notes
sn get u_demande_acces UDA0001234
sn get RITM0012345 --format json | jq -r .state
```

### `sn kb`

```text
sn kb [OPTIONS] TEXTE...
```

Recherche d'articles de connaissance. Utilise d'abord l'API Knowledge Management
(`/api/sn_km_api/knowledge/articles`), qui renvoie un extrait (`snippet`) et un
score de pertinence. Si cette API n'est pas disponible (HTTP 400, 403 ou 404),
`sn` se replie sur la table `kb_knowledge`, limitée aux articles publiés
(`workflow_state=published`). Les liens pointent vers `kb_view.do`.

| Option | Défaut | Description |
|---|---|---|
| `-n`, `--limit N` | `10` | Nombre maximal d'articles. |
| `--url` | désactivé | Ajoute une colonne `url`. |
| `--format table\|json\|csv` | `table` | Voir [Formats de sortie](#formats-de-sortie). |

`sn kb` n'a pas d'option `-q` ni `-f`. Pour filtrer les articles (base de
connaissances, catégorie...), utilisez :

```bash
sn search "vpn" -t kb_knowledge -q "workflow_state=published^kb_knowledge_base.title=IT"
```

### `sn ci-link`

```text
sn ci-link [OPTIONS] OFFRE CI
```

Indique si une offre de service est liée à un élément de configuration (CI) et
donne **le plus court chemin** de relations entre les deux.

`OFFRE` et `CI` : nom exact, partie unique du nom, ou `sys_id`. L'offre est
cherchée dans `service_offering`, le CI dans `cmdb_ci` (toutes classes). Si
plusieurs fiches correspondent, la commande échoue (code `1`) et liste les
candidats avec leur classe et leur `sys_id`.

Liens reconnus, dans cet ordre :

| `link` | Signification |
|---|---|
| `same-record` | L'offre et le CI sont la même fiche. |
| `offering-parent` | Le CI est le service parent de l'offre (`service_offering.parent`). |
| `direct` | Une relation `cmdb_rel_ci` relie l'offre et le CI. |
| `indirect` | Chaîne de plusieurs relations `cmdb_rel_ci`, éventuellement par une carte de service (`svc_ci_assoc` : un service applicatif contient le CI). |

La recherche est bidirectionnelle (vers le bas depuis l'offre, vers le haut depuis
le CI), par niveaux, avec une requête groupée (`parentIN...`) par niveau et par
lot de 100 CI.

| Option | Défaut | Description |
|---|---|---|
| `--offering-table TABLE` | `service_offering` | Table de l'offre (`cmdb_ci_service` pour un service). |
| `--ci-table TABLE` | `cmdb_ci` | Table du CI ; une classe (`cmdb_ci_server`...) lève une ambiguïté de nom. |
| `-d`, `--depth N` | `6` | Nombre maximal de relations entre l'offre et le CI (1 à 12). |
| `--direction down\|any` | `down` | `down` : le CI doit être en aval de l'offre (parent → enfant). `any` : relations suivies dans les deux sens. |
| `--no-service-map` | désactivé | N'utilise pas les cartes de service (`svc_ci_assoc`). |
| `--exclude-type TEXTE` | aucun | Ignore les types de relation dont le libellé contient ce texte. Répétable. |
| `--max-nodes N` | `5000` | Arrêt de sécurité : nombre de CI atteints par côté. |
| `--format table\|json` | `table` | `json` : objet avec `linked`, `link`, `depth`, `chain`, `steps`, `offering`, `ci`, `search`. |

Le champ `search` du JSON décrit le parcours : `exhausted` (tout le graphe
accessible a été vu), `truncated` (limite atteinte), `queries`,
`reached_from_offering`, `reached_from_ci`, `service_map` (`false` si
`svc_ci_assoc` n'est pas lisible) et `warnings`. Le code de sortie est `0` que le
lien existe ou non.

```bash
sn ci-link "Messagerie - Standard" SRV-MAIL-01
sn ci-link "Paie" PAIE-DB --ci-table cmdb_ci_db_instance --format json
sn ci-link 1a2b...  3c4d... --direction any --exclude-type "Connects to"
```

### `sn open`

```text
sn open NUMÉRO
sn open TABLE NUMÉRO|SYS_ID
```

Mêmes arguments que `sn get`. Affiche l'URL de la fiche puis l'ouvre dans le
navigateur (le navigateur Windows sous WSL). Aucune option.

### `sn api`

```text
sn api [OPTIONS] CHEMIN
```

Appelle n'importe quel endpoint REST de l'instance avec votre session. Le `/`
initial du chemin est facultatif. La réponse est affichée en JSON indenté (ou
telle quelle si ce n'est pas du JSON).

| Option | Défaut | Description |
|---|---|---|
| `-X`, `--method MÉTHODE` | `GET` | Méthode HTTP (`GET`, `POST`, `PATCH`, `PUT`, `DELETE`). |
| `-p`, `--param CLÉ=VALEUR` | aucun | Paramètre d'URL. Répétable. Seul le premier `=` sépare la clé de la valeur, donc une requête encodée passe telle quelle. |
| `-d`, `--data JSON` | aucun | Corps de la requête, en JSON. |

`sn api` donne accès à tous les paramètres de la Table API, y compris ceux que
`sn query` n'expose pas :

| Paramètre | Rôle |
|---|---|
| `sysparm_query` | Requête encodée (même syntaxe que `-q`). |
| `sysparm_fields` | Champs, séparés par des virgules. |
| `sysparm_limit`, `sysparm_offset` | Pagination. |
| `sysparm_display_value` | `true`, `false` ou `all` (valeur brute **et** valeur d'affichage). |
| `sysparm_exclude_reference_link` | `true` pour retirer les liens des références. |

```bash
sn api /api/now/table/sys_user_group -p sysparm_limit=5
sn api api/now/table/incident -p "sysparm_query=active=true^priority=1" -p sysparm_fields=number,short_description
sn api /api/now/table/incident -p sysparm_limit=1 -p sysparm_display_value=all
# Compter sans télécharger les fiches (API Aggregate) :
sn api /api/now/stats/incident -p sysparm_count=true -p "sysparm_query=active=true"
sn api /api/now/stats/incident -p sysparm_count=true -p sysparm_group_by=priority -p "sysparm_query=active=true"
```

> `-X POST/PATCH/PUT/DELETE` modifie réellement des données sur l'instance, avec
> vos droits.

---

## Requêtes encodées (`-q`)

`-q` accepte une **requête encodée** ServiceNow (*encoded query*), le même format
que le paramètre `sysparm_query` de l'API et que l'URL d'une liste filtrée.

| Commande | Rôle de `-q` |
|---|---|
| `sn query TABLE -q ...` | C'est toute la requête. |
| `sn search TEXTE -q ...` | Filtre ajouté après la condition de recherche : `123TEXTQUERY321=<texte>^<votre requête>`. |

### La façon la plus simple de l'obtenir

1. Dans ServiceNow, ouvrez la liste de la table (ex. `incident.list`).
2. Construisez le filtre avec le générateur de conditions, puis *Run*.
3. Clic droit sur le dernier élément du fil d'Ariane du filtre > *Copy query*.
4. Collez-la entre apostrophes : `sn query incident -q '<requête copiée>'`.

### Structure

Une requête est une suite de conditions `<champ><opérateur><valeur>`, reliées par
des séparateurs :

| Séparateur | Sens | Exemple |
|---|---|---|
| `^` | ET | `active=true^priority=1` |
| `^OR` | OU, avec la condition précédente | `priority=1^ORpriority=2` |
| `^NQ` | OU entre deux blocs complets (*new query*) | `active=true^priority=1^NQactive=true^impact=1` |
| `^ORDERBY<champ>` | tri croissant | `active=true^ORDERBYnumber` |
| `^ORDERBYDESC<champ>` | tri décroissant | `active=true^ORDERBYDESCsys_updated_on` |

`^OR` est prioritaire sur `^` : `active=true^priority=1^ORpriority=2` signifie
`active ET (priorité 1 OU 2)`. Il n'y a pas de parenthèses : pour
`(A ET B) OU (C ET D)`, utilisez `^NQ`.

Il n'y a **pas d'espaces** autour des opérateurs, et les valeurs ne sont **pas
entre guillemets** : `short_descriptionLIKEmot de passe` est valide.

### Opérateurs

| Opérateur | Sens | Exemple |
|---|---|---|
| `=` | égal à | `state=2` |
| `!=` | différent de | `state!=7` |
| `<` `<=` `>` `>=` | comparaison (nombres, dates) | `priority<=2` |
| `LIKE` | contient | `short_descriptionLIKEvpn` |
| `NOT LIKE` | ne contient pas | `short_descriptionNOT LIKEtest` |
| `STARTSWITH` | commence par | `numberSTARTSWITHINC001` |
| `ENDSWITH` | se termine par | `emailENDSWITH@exemple.com` |
| `IN` | dans la liste (virgules) | `stateIN1,2,3` |
| `NOT IN` | hors de la liste | `stateNOT IN6,7,8` |
| `ISEMPTY` | vide (sans valeur) | `assigned_toISEMPTY` |
| `ISNOTEMPTY` | renseigné | `resolved_atISNOTEMPTY` |
| `BETWEEN` | entre deux valeurs, séparées par `@` | `priorityBETWEEN1@3` |
| `SAMEAS` / `NSAMEAS` | égal / différent d'un autre champ | `opened_bySAMEAScaller_id` |
| `RELATIVEGT` / `RELATIVELT` | plus récent / plus ancien qu'une durée relative | `sys_updated_onRELATIVEGT@hour@ago@24` |

`LIKE`, `STARTSWITH` et `ENDSWITH` ne sont pas sensibles à la casse dans la
configuration habituelle de ServiceNow.

### Valeurs : stockées, pas affichées

Les conditions portent sur la **valeur stockée**, pas sur le libellé affiché :

* **Listes de choix** : `state=2`, pas `state=In Progress`.
* **Références** (utilisateur, groupe, CI...) : le `sys_id`, ou mieux, un
  **dot-walk** vers un champ lisible : `assignment_group.name=Network`,
  `caller_id.user_name=jdoe`, `cmdb_ci.name=SRV-APP-01`.
* **Booléens** : `true` / `false`.

Pour trouver la valeur stockée :

```bash
sn query incident -q "number=INC0012345" --raw-values -f state,priority,assignment_group
# Toutes les valeurs d'une liste de choix (ici incident.state) :
sn query sys_choice -q "name=incident^element=state^inactive=false" -f value,label,language -n 100
```

Valeurs **par défaut** de ServiceNow (votre instance peut les avoir modifiées,
vérifiez avec la commande ci-dessus) :

| Table.champ | Valeurs |
|---|---|
| `incident.state` | `1` New, `2` In Progress, `3` On Hold, `6` Resolved, `7` Closed, `8` Canceled |
| `priority` | `1` Critical, `2` High, `3` Moderate, `4` Low, `5` Planning |
| `impact`, `urgency` | `1` High, `2` Medium, `3` Low |
| `sc_req_item.state`, `sc_task.state` | `-5` Pending, `1` Open, `2` Work in Progress, `3` Closed Complete, `4` Closed Incomplete, `7` Closed Skipped |
| `change_request.state` | `-5` New, `-4` Assess, `-3` Authorize, `-2` Scheduled, `-1` Implement, `0` Review, `3` Closed, `4` Canceled |
| `change_request.type` | `normal`, `standard`, `emergency` |
| `problem.state` | `101` New, `102` Assess, `103` Root Cause Analysis, `104` Fix in Progress, `106` Resolved, `107` Closed |
| `kb_knowledge.workflow_state` | `draft`, `review`, `published`, `pending_retirement`, `retired`, `outdated` |
| `active` (toutes les tâches) | `true` tant que la fiche n'est pas fermée |

### Valeurs dynamiques (`javascript:`)

Une valeur peut être une expression `javascript:` évaluée par l'instance :

| Expression | Valeur |
|---|---|
| `javascript:gs.getUserID()` | votre `sys_id` d'utilisateur |
| `javascript:getMyGroups()` | vos groupes |
| `javascript:gs.beginningOfToday()` / `gs.endOfToday()` | début / fin d'aujourd'hui |
| `javascript:gs.beginningOfThisWeek()` / `gs.beginningOfThisMonth()` | début de la semaine / du mois |
| `javascript:gs.beginningOfLastMonth()` / `gs.endOfLastMonth()` | mois précédent |
| `javascript:gs.daysAgoStart(N)` / `gs.daysAgoEnd(N)` | début / fin du jour il y a N jours |
| `javascript:gs.hoursAgoStart(N)` / `gs.minutesAgoStart(N)` | il y a N heures / minutes |
| `javascript:gs.dateGenerate('AAAA-MM-JJ','HH:MM:SS')` | date fixe |

```text
assigned_to=javascript:gs.getUserID()
assignment_group=javascript:getMyGroups()
sys_created_on>=javascript:gs.daysAgoStart(7)
sys_created_onBETWEENjavascript:gs.dateGenerate('2026-01-01','00:00:00')@javascript:gs.dateGenerate('2026-01-31','23:59:59')
```

Dates relatives avec `RELATIVEGT` / `RELATIVELT` :
`<champ>RELATIVEGT@<unité>@ago@<N>`, avec l'unité `minute`, `hour`, `dayofweek`
(jours), `month`, `quarter` ou `year`. Exemple : `sys_updated_onRELATIVEGT@dayofweek@ago@3`
= modifié dans les 3 derniers jours.

### Exemples prêts à l'emploi

| Besoin | Requête |
|---|---|
| Mes incidents ouverts | `active=true^assigned_to=javascript:gs.getUserID()` |
| Incidents ouverts de mes groupes, non assignés | `active=true^assignment_group=javascript:getMyGroups()^assigned_toISEMPTY` |
| P1 et P2 ouverts | `active=true^priority<=2` |
| Ouverts depuis plus de 30 jours | `active=true^opened_at<javascript:gs.daysAgoStart(30)` |
| Modifiés dans les dernières 24 h | `sys_updated_onRELATIVEGT@hour@ago@24` |
| Créés aujourd'hui | `sys_created_on>=javascript:gs.beginningOfToday()` |
| Résolus le mois dernier | `resolved_atBETWEENjavascript:gs.beginningOfLastMonth()@javascript:gs.endOfLastMonth()` |
| Pour un groupe donné | `assignment_group.name=Service Desk` |
| Pour un appelant | `caller_id.user_name=jdoe` |
| Liés à un CI | `cmdb_ci.name=SRV-APP-01` |
| RITM d'un article du catalogue | `cat_item.name=Accès VPN^active=true` |
| Mes demandes en cours | `requested_for=javascript:gs.getUserID()^active=true` |
| Changements d'urgence planifiés à partir d'aujourd'hui | `type=emergency^start_date>=javascript:gs.beginningOfToday()` |
| Articles publiés d'une base | `workflow_state=published^kb_knowledge_base.title=IT` |
| Tâches de toutes sortes qui me sont assignées | (table `task`) `active=true^assigned_to=javascript:gs.getUserID()` |

### Pièges

* **Shell** : mettez toujours la requête **entre apostrophes** (`'...'`). `<`, `>`,
  `|`, `&`, `(`, `)` et les espaces sont interprétés par bash sinon. Les guillemets
  doubles conviennent tant que la requête ne contient ni `$` ni backquote.
* **Champ mal orthographié** : par défaut, ServiceNow **ignore** une condition sur
  un champ inexistant au lieu de renvoyer une erreur. `prioirty=1` renvoie donc
  toute la table (limitée par `-n`). Si un résultat paraît trop large, vérifiez
  les noms de champs. (Ce comportement dépend de la propriété système
  `glide.invalid_query.returns_no_rows`.)
* **`^` dans une valeur** : impossible à échapper. Préférez `LIKE` sur un morceau
  de texte qui ne le contient pas.
* **Avec `sn search`**, la requête est ajoutée par un ET à la condition de
  recherche. Un `^NQ` dans `-q` crée un second bloc **sans** la condition de
  recherche (`texte OU bloc`) et ramène des fiches sans rapport. Un `^OR` en tête
  de `-q` fait un OU avec la recherche. Pour une logique complexe, utilisez
  `sn query` et `LIKE`.
* **`ORDERBY` avec `sn search`** remplace le tri par pertinence.
* **Dates littérales** (`sys_created_on>=2026-01-01 00:00:00`) : selon la
  configuration, l'instance peut les interpréter dans le fuseau horaire de
  votre compte ou en UTC. Les fonctions `javascript:gs.*` évitent la question.
* **`IN`** prend des valeurs stockées séparées par des virgules, sans espace :
  `stateIN1,2,3`.

---

## Champs par défaut

Sans `-f`, `sn search` et `sn query` affichent ces champs :

| Table | Champs |
|---|---|
| `incident` | number, short_description, state, priority, assigned_to, assignment_group, sys_updated_on |
| `sc_req_item` | number, short_description, state, cat_item, requested_for, sys_updated_on |
| `sc_request` | number, short_description, request_state, requested_for, sys_updated_on |
| `sc_task` | number, short_description, state, assigned_to, sys_updated_on |
| `change_request` | number, short_description, state, type, start_date, assigned_to |
| `problem` | number, short_description, state, priority, assigned_to, sys_updated_on |
| `kb_knowledge` | number, short_description, workflow_state, kb_knowledge_base, sys_updated_on |
| `task` | number, short_description, state, sys_class_name, assigned_to, sys_updated_on |
| `sys_user` | user_name, name, email, title, department |
| `sys_user_group` | name, description, manager, email |
| `cmdb_ci` | name, sys_class_name, operational_status, owned_by |
| autre table | number, short_description, state, sys_updated_on |

Pour une table qui n'a pas ces champs (ex. une table personnalisée), passez `-f`,
sinon les colonnes restent vides.

---

## Configuration et session

### `sn configure`

Crée ou modifie le profil courant (choisi avec `-P`). Sans option de
modification, ou avec `--show`, affiche la configuration effective en JSON.

| Option | Description |
|---|---|
| `-i`, `--instance VALEUR` | Instance : `acme` (=> `https://acme.service-now.com`), un nom d'hôte, ou une URL complète. |
| `--auth-method browser\|oauth\|cookie` | Méthode utilisée par `sn login` sans `-m`. Défaut : `browser`. |
| `--sso-login-path CHEMIN` | Page qui démarre le SSO, ex. `login_with_sso.do?glide_sso_id=<sys_id>`. Une chaîne vide (`""`) l'efface. |
| `--oauth-client-id ID` | Client ID d'une application OAuth publique enregistrée sur l'instance. |
| `--oauth-redirect-uri URI` | URI de redirection de cette application. Défaut : `http://localhost:8765/callback`. |
| `--oauth-scope SCOPE` | Scope OAuth (généralement vide ou `useraccount`). |
| `--tables LISTE` | Tables par défaut de `sn search`, séparées par des virgules. |
| `--make-default` | Fait de ce profil le profil par défaut. |
| `--show` | Affiche la configuration effective. |

Fichier : `~/.config/sncli/config.json` (ou `$XDG_CONFIG_HOME/sncli/`, ou
`$SNCLI_CONFIG_DIR`).

```bash
sn configure -i acme --tables incident,sc_req_item,kb_knowledge
sn -P dev configure -i acme-dev --make-default
sn configure --show
```

### `sn login`

| Option | Défaut | Description |
|---|---|---|
| `-m`, `--method browser\|oauth\|cookie` | méthode du profil | `browser` : SSO dans une fenêtre de navigateur. `oauth` : OAuth + PKCE. `cookie` : en-tête `Cookie` collé. |
| `-i`, `--instance VALEUR` | instance du profil | Remplace l'instance pour cette connexion. |
| `--sdk-client` | désactivé | OAuth avec le client public du SDK ServiceNow (copier-coller d'un code). Implique `-m oauth`. Voir les réserves dans le README. |
| `--cookie VALEUR` | demandé (masqué) | En-tête `Cookie` pour `-m cookie`. |
| `--user-token VALEUR` | déduit | `X-UserToken` (`g_ck`) pour `-m cookie`. Normalement récupéré automatiquement. |
| `--timeout SECONDES` | `300` | Délai maximal pour terminer la connexion. |

### `sn logout`

| Option | Description |
|---|---|
| `--forget-browser` | Supprime aussi le profil de navigateur (cookies de l'IdP), ce qui empêche le renouvellement silencieux. |

### `sn whoami`

Interroge l'instance et affiche l'utilisateur connecté (vérifie que la session
est valide).

| Option | Défaut | Description |
|---|---|---|
| `--format table\|json\|csv` | `table` | `csv` s'affiche comme `table`. |

### `sn status`

Affiche en JSON le profil, l'instance, la méthode d'authentification, la date
d'obtention et l'expiration des jetons, **sans** contacter l'instance. Aucune
option.

---

## Formats de sortie

| Format | Contenu | Remarques |
|---|---|---|
| `table` | Tableau lisible, un par table. | Le titre et le total (`N result(s) ...`) sont écrits sur la sortie d'erreur : `sn search ... > fichier` ne contient que les tableaux. |
| `json` | JSON indenté, UTF-8. | Contient toujours `sys_id` et `_url`, même sans `--url`. |
| `csv` | CSV avec ligne d'en-tête. | `_url` seulement avec `--url`. |

Forme du JSON selon la commande :

* `sn search` : une liste avec un objet par table :
  ```json
  [{"table": "incident", "rows": [{"number": "INC0010001", "...": "...", "sys_id": "...", "_url": "..."}],
    "error": null, "mode": "text"}]
  ```
  `mode` vaut `text` (mots-clés) ou `like` ; `error` contient le message si la
  table a échoué.
* `sn query`, `sn kb` : une liste de lignes.
* `sn get`, `sn whoami` : un objet.

Pour `sn search --format csv`, chaque table produit son propre bloc CSV (avec sa
ligne d'en-tête) et une colonne `_table` indique la table d'origine. Pour un CSV
unique, interrogez une seule table (`-t`) ou utilisez `sn query`.

---

## Variables d'environnement

| Variable | Effet |
|---|---|
| `SN_PROFILE` | Profil par défaut (équivaut à `-P`). |
| `SN_INSTANCE` | Remplace l'instance du profil. Obligatoire avec les trois variables suivantes. |
| `SN_BEARER_TOKEN` | Jeton OAuth fourni directement (rien n'est stocké). |
| `SN_SESSION_COOKIE` | En-tête `Cookie` d'une session fourni directement. |
| `SN_USER_TOKEN` | `X-UserToken` associé à `SN_SESSION_COOKIE`. |
| `SN_OAUTH_CLIENT_SECRET` | Secret d'un client OAuth confidentiel. |
| `SN_CA_BUNDLE` | Fichier d'autorités de certification (proxy d'entreprise qui inspecte le TLS). `REQUESTS_CA_BUNDLE` et `SSL_CERT_FILE` sont aussi lus. |
| `SN_INSECURE=1` | Désactive la vérification TLS. À éviter. |
| `SN_BROWSER_CHANNEL` | Navigateur installé à utiliser pour le SSO : `chrome`, `msedge`. |
| `SN_BROWSER_PATH` | Chemin d'un exécutable Chrome / Chromium. Prioritaire sur `SN_BROWSER_CHANNEL`. |
| `SN_BROWSER_NO_SANDBOX` | Lance le navigateur avec `--no-sandbox` (conteneurs). |
| `SNCLI_NO_KEYRING` | N'utilise pas le trousseau système ; les identifiants vont dans un fichier `0600`. |
| `SNCLI_CONFIG_DIR` | Dossier de configuration (défaut `~/.config/sncli`). |
| `SNCLI_STATE_DIR` | Dossier d'état : identifiants, profil de navigateur (défaut `~/.local/state/sncli`). |

---

## Codes de sortie

| Code | Cas |
|---|---|
| `0` | Succès. Pour `sn search`, aussi quand certaines tables ont échoué (avertissement sur la sortie d'erreur). |
| `1` | Erreur : non connecté, session expirée, erreur HTTP de l'instance, fiche introuvable, table impossible à déduire. |
| `2` | Mauvaise utilisation : option inconnue, argument manquant, `sn login` sans instance configurée. |
