# Problèmes (`PRB`, table `problem`)

Un problème est la cause sous-jacente d'un ou plusieurs incidents. Il porte
l'analyse de cause racine, le contournement (workaround) et le correctif. Un
problème dont la cause et le contournement sont connus est une **erreur connue**
(known error).

## Champs utiles

| Champ | Contenu | Remarque |
|---|---|---|
| `number` | `PRB0040001` | |
| `short_description`, `description` | Résumé, détail | |
| `state` | État (voir plus bas) | |
| `priority`, `impact`, `urgency` | | |
| `assignment_group`, `assigned_to` | Analyse | |
| `cmdb_ci`, `business_service` | CI / service concerné | |
| `category`, `subcategory` | Classification | |
| `known_error` | `true` si erreur connue | |
| `workaround` | Contournement | à donner en priorité aux équipes de support |
| `cause_notes` | Cause racine | |
| `fix_notes` | Correctif | |
| `rfc` | Changement qui applique le correctif | |
| `first_reported_by_task` | Premier incident à l'origine du problème | |
| `related_incidents` | Nombre d'incidents liés (compteur) | peut être absent selon la version |
| `resolution_code` | Mode de résolution (`fix_applied`, `risk_accepted`, `duplicate`, `canceled`) | instances récentes |
| `opened_at`, `resolved_at`, `closed_at` | Dates | |
| `work_notes`, `comments` | Journaux | |

États par défaut (instances récentes) : `101` New, `102` Assess,
`103` Root Cause Analysis, `104` Fix in Progress, `106` Resolved, `107` Closed.
Les anciennes instances utilisent `1` Open, `2` Known Error, `3` Pending Change,
`4` Closed/Resolved : vérifiez avec
`sn query sys_choice -q 'name=problem^element=state^inactive=false' -f value,label -n 30 --format json`.

## Lire un problème

```bash
sn get PRB0040001 -f number,short_description,description,state,priority,known_error,cmdb_ci,business_service,assignment_group,assigned_to,workaround,cause_notes,fix_notes,rfc,first_reported_by_task,opened_at,resolved_at --format json

sn get PRB0040001 -f work_notes,comments --format json
```

## Éléments liés

```bash
# Incidents rattachés
sn query incident -q 'problem_id.number=PRB0040001' \
  -f number,short_description,state,priority,opened_at,cmdb_ci,assignment_group -o -opened_at -n 100 --format json

# Nombre d'incidents rattachés (sans les télécharger)
sn api /api/now/stats/incident -p sysparm_count=true -p 'sysparm_query=problem_id.number=PRB0040001'

# Tâches du problème
sn query problem_task -q 'problem.number=PRB0040001' \
  -f number,short_description,state,problem_task_type,assigned_to,sys_updated_on --format json

# Changement correctif (si rfc est renseigné)
sn get change_request <numéro rfc> -f number,short_description,state,start_date,end_date,close_code --format json

# Articles KB issus du problème (erreurs connues publiées)
sn query kb_knowledge -q 'source.number=PRB0040001' -f number,short_description,workflow_state --format json
```

La relation KB ↔ problème dépend de la configuration (`source`, `m2m_kb_task`) ;
si la première requête ne renvoie rien, essayez
`sn query m2m_kb_task -q 'task.number=PRB0040001' -f kb_knowledge.number,kb_knowledge.short_description --format json`.

## Erreurs connues et contournements

```bash
# Erreurs connues ouvertes, avec contournement
sn query problem -q 'active=true^known_error=true' \
  -f number,short_description,cmdb_ci,workaround,state -o -sys_updated_on --format json

# Erreur connue pour un symptôme
sn search "<mots-clés>" -t problem -q 'known_error=true' \
  -f number,short_description,workaround,cause_notes,state --format json

# Problèmes ouverts sur un CI
sn query problem -q 'active=true^cmdb_ci.name=SRV-APP-01' --format json
```

Face à un incident, cherchez toujours une erreur connue **avant** de proposer une
résolution : le contournement documenté est la réponse attendue.

## Détecter des candidats problèmes

Des incidents répétitifs sans problème rattaché sont des candidats :

```bash
# CI les plus touchés sur 30 jours, incidents sans problème
sn api /api/now/stats/incident -p sysparm_count=true -p sysparm_group_by=cmdb_ci \
  -p sysparm_display_value=true \
  -p 'sysparm_query=opened_at>=javascript:gs.daysAgoStart(30)^problem_idISEMPTY^cmdb_ciISNOTEMPTY'

# Puis le détail pour un CI
sn query incident -q 'opened_at>=javascript:gs.daysAgoStart(30)^problem_idISEMPTY^cmdb_ci.name=<CI>' \
  -f number,short_description,category,opened_at,close_notes -o -opened_at -n 50 --format json
```

Regroupez ensuite par symptôme (`short_description`, `category`, `close_notes`)
et vérifiez qu'aucun problème ouvert ne couvre déjà ce symptôme (`sn search ... -t problem`).

## Statistiques

```bash
# Problèmes ouverts par état
sn api /api/now/stats/problem -p sysparm_count=true -p sysparm_group_by=state \
  -p sysparm_display_value=true -p 'sysparm_query=active=true'

# Problèmes ouverts depuis plus de 90 jours
sn query problem -q 'active=true^opened_at<javascript:gs.daysAgoStart(90)' -o opened_at --format json
```

## Pièges

* Le champ de l'incident est `problem_id`, pas `problem`. À l'inverse, la tâche
  de problème pointe par `problem` (`problem_task.problem`).
* `workaround`, `cause_notes`, `fix_notes` peuvent être vides alors que
  l'information est dans les work notes : lisez le journal avant de conclure.
* `problem_task` n'a pas de préréglage de champs dans `sn` : passez `-f`.
