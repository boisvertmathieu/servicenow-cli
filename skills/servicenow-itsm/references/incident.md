# Incidents (`INC`, table `incident`)

Un incident est une interruption ou une dégradation d'un service, ouverte par un
appelant ou par la supervision.

## Champs utiles

| Champ | Contenu | Remarque |
|---|---|---|
| `number` | `INC0012345` | |
| `short_description`, `description` | Résumé, détail | |
| `state` | État (voir plus bas) | |
| `priority`, `impact`, `urgency` | `1` (le plus grave) à `5` / `3` | priorité calculée depuis impact × urgence |
| `caller_id` | Appelant (`sys_user`) | dot-walk : `caller_id.user_name`, `caller_id.name` |
| `opened_by`, `opened_at` | Créateur, date d'ouverture | |
| `assignment_group`, `assigned_to` | Groupe et personne assignés | `assignment_group.name=...` |
| `category`, `subcategory` | Classification | valeurs propres à l'instance (`sys_choice`) |
| `cmdb_ci` | CI touché | `cmdb_ci.name=SRV-APP-01` |
| `business_service` | Service métier | `business_service.name=...` |
| `contact_type` | Canal (phone, email, self-service...) | |
| `hold_reason` | Raison de mise en attente (state `3`) | |
| `reassignment_count`, `reopen_count` | Nombre de réassignations / réouvertures | indicateurs de ping-pong |
| `resolved_at`, `resolved_by`, `closed_at` | Résolution / fermeture | |
| `close_code`, `close_notes` | Code et notes de résolution | `close_notes` = la solution appliquée |
| `problem_id` | Problème lié (`PRB`) | |
| `rfc` | Changement qui a **résolu** l'incident | |
| `caused_by` | Changement qui a **causé** l'incident | |
| `parent_incident` | Incident parent (incident majeur) | enfants : `parent_incident.number=INC...` |
| `major_incident_state` | État d'incident majeur, si le module est actif | |
| `work_notes`, `comments` | Journaux interne / visible par l'appelant | voir « Journal » |
| `sla_due` | Échéance SLA | détails dans `task_sla` |

États par défaut : `1` New, `2` In Progress, `3` On Hold, `6` Resolved,
`7` Closed, `8` Canceled. `active=true` couvre New, In Progress et On Hold.

## Lire un incident

```bash
# Synthèse
sn get INC0012345 -f number,short_description,description,state,priority,caller_id,assignment_group,assigned_to,cmdb_ci,category,opened_at,resolved_at,close_code,close_notes,problem_id,rfc,caused_by,parent_incident --format json

# Journal complet (texte concaténé, le plus récent en premier)
sn get INC0012345 -f work_notes,comments --format json
```

Le journal détaillé, entrée par entrée (auteur et date), se lit dans
`sys_journal_field`. Il faut le `sys_id` de l'incident (donné par `sn get`) :

```bash
sn query sys_journal_field -q 'element_id=<sys_id>^elementINwork_notes,comments' \
  -f element,value,sys_created_by,sys_created_on -o sys_created_on -n 200 --format json
```

Cette table est parfois fermée par ACL : repliez-vous alors sur
`-f work_notes,comments`.

Historique des réassignations et des états (si `sys_audit` est lisible) :

```bash
sn query sys_audit -q 'documentkey=<sys_id>^fieldnameINstate,assignment_group,assigned_to,priority' \
  -f fieldname,oldvalue,newvalue,user,sys_created_on -o sys_created_on -n 100 --format json
```

`sys_audit` renvoie des valeurs stockées (sys_id pour les références).

## Files de travail courantes

```bash
# Mes incidents ouverts, les plus récents d'abord
sn query incident -q 'active=true^assigned_to=javascript:gs.getUserID()' -o -sys_updated_on --format json

# Non assignés dans mes groupes
sn query incident -q 'active=true^assignment_group=javascript:getMyGroups()^assigned_toISEMPTY' -o -opened_at --format json

# P1/P2 ouverts
sn query incident -q 'active=true^priority<=2' -o priority --format json

# File d'un groupe, en attente depuis plus de 5 jours sans mise à jour
sn query incident -q 'active=true^assignment_group.name=Service Desk^sys_updated_on<javascript:gs.daysAgoStart(5)' -o sys_updated_on --format json

# Incidents ouverts sur un CI
sn query incident -q 'active=true^cmdb_ci.name=SRV-APP-01' --format json

# Enfants d'un incident majeur
sn query incident -q 'parent_incident.number=INC0012345' --format json
```

## Incidents similaires (résolutions passées)

Pour proposer une solution, cherchez des incidents **résolus** qui décrivent le
même symptôme, puis lisez leur `close_notes` :

```bash
sn search "<2 à 4 mots-clés du symptôme>" -t incident -q 'stateIN6,7' -n 10 \
  -f number,short_description,close_code,close_notes,resolved_at,assignment_group,problem_id --format json
```

* Choisissez des mots-clés discriminants (message d'erreur, nom d'application,
  code), pas la phrase entière.
* Sur un CI précis : ajoutez `^cmdb_ci.name=<CI>` au `-q`.
* Plusieurs incidents similaires récents sans `problem_id` : c'est un candidat
  problème (voir [problem.md](problem.md)).
* Complétez toujours par une recherche KB (voir [knowledge.md](knowledge.md)).

## Statistiques

```bash
# Ouverts par groupe
sn api /api/now/stats/incident -p sysparm_count=true -p sysparm_group_by=assignment_group \
  -p sysparm_display_value=true -p 'sysparm_query=active=true'

# Créés sur 30 jours par CI (top des CI bruyants)
sn api /api/now/stats/incident -p sysparm_count=true -p sysparm_group_by=cmdb_ci \
  -p sysparm_display_value=true -p 'sysparm_query=opened_at>=javascript:gs.daysAgoStart(30)^cmdb_ciISNOTEMPTY'

# Par priorité et état
sn api /api/now/stats/incident -p sysparm_count=true -p sysparm_group_by=priority,state \
  -p sysparm_display_value=true -p 'sysparm_query=active=true'
```

Réponse : `result` est une liste d'objets
`{"stats": {"count": "12"}, "groupby_fields": [{"field", "value", "display_value"}]}`.
Sans `sysparm_group_by`, `result.stats.count`. Triez vous-même par `count`.

Autres agrégats : `sysparm_avg_fields=reassignment_count`,
`sysparm_max_fields=...`, `sysparm_sum_fields=...`.

## SLA

```bash
sn query task_sla -q 'task.number=INC0012345' \
  -f sla,stage,has_breached,planned_end_time,business_percentage --format json
```

## Pièges

* `assigned_to=<nom>` ne marche pas : utilisez `assigned_to.user_name=jdoe` ou
  `assigned_to.name=Jean Dupont`.
* `close_code` a des valeurs propres à l'instance : vérifiez-les dans `sys_choice`
  (`name=incident^element=close_code`).
* Un incident `Resolved` (6) se ferme automatiquement après quelques jours ;
  « résolus » = `stateIN6,7`.
* `caused_by` et `rfc` pointent tous deux vers `change_request`, mais avec un sens
  opposé : ne les confondez pas.
