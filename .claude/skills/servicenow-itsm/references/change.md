# Changements (`CHG`, table `change_request`)

Un changement est une modification planifiée d'un service ou d'un CI. Il se
décompose en tâches (`CTASK`, table `change_task`) et passe par des approbations
(table `sysapproval_approver`).

## Champs utiles

| Champ | Contenu | Remarque |
|---|---|---|
| `number` | `CHG0030001` | |
| `short_description`, `description` | Résumé, détail | |
| `type` | `normal`, `standard`, `emergency` | |
| `state` | État (voir plus bas) | |
| `approval` | `not requested`, `requested`, `approved`, `rejected` | |
| `risk`, `impact`, `priority` | Risque (`1` Very High ... `4` Low, selon l'instance), impact, priorité | |
| `start_date`, `end_date` | Fenêtre planifiée | |
| `work_start`, `work_end` | Réalisation effective | |
| `cab_required`, `cab_date`, `cab_delegate` | Passage en CAB | |
| `cmdb_ci` | CI principal | autres CI dans `task_ci` |
| `business_service` | Service métier | |
| `assignment_group`, `assigned_to` | Réalisation | |
| `requested_by` | Demandeur | |
| `category` | Catégorie | |
| `justification` | Raison du changement | |
| `implementation_plan`, `backout_plan`, `test_plan` | Plans | texte long |
| `risk_impact_analysis` | Analyse d'impact | |
| `close_code`, `close_notes` | Résultat (`successful`, `successful_issues`, `unsuccessful`, selon l'instance) | |
| `on_hold`, `on_hold_reason` | Mise en attente | |
| `conflict_status`, `conflict_last_run` | Résultat de la détection de conflits | |
| `std_change_producer_version` | Modèle de changement standard | |
| `chg_model` | Modèle de changement (instances récentes) | |
| `work_notes`, `comments` | Journaux | |

États par défaut : `-5` New, `-4` Assess, `-3` Authorize, `-2` Scheduled,
`-1` Implement, `0` Review, `3` Closed, `4` Canceled. Les valeurs sont
**négatives** : `state<0` = pas encore en revue ni fermé ; `active=true` reste
le filtre le plus sûr pour « en cours ».

## Lire un changement

```bash
sn get CHG0030001 -f number,short_description,type,state,approval,risk,impact,start_date,end_date,work_start,work_end,cmdb_ci,business_service,assignment_group,assigned_to,requested_by,cab_required,cab_date,close_code,close_notes --format json

# Plans (texte long : à demander seulement si nécessaire)
sn get CHG0030001 -f justification,implementation_plan,backout_plan,test_plan,risk_impact_analysis --format json

# Journal
sn get CHG0030001 -f work_notes,comments --format json
```

## Éléments liés

```bash
# Tâches du changement
sn query change_task -q 'change_request.number=CHG0030001' \
  -f number,short_description,state,assignment_group,assigned_to,planned_start_date,planned_end_date -o planned_start_date --format json

# Approbations
sn query sysapproval_approver -q 'sysapproval.number=CHG0030001' \
  -f approver,state,comments,sys_updated_on -o sys_updated_on --format json

# CI affectés (en plus de cmdb_ci)
sn query task_ci -q 'task.number=CHG0030001' -f ci_item,ci_item.sys_class_name --format json

# Incidents CAUSÉS par ce changement
sn query incident -q 'caused_by.number=CHG0030001' --format json

# Incidents et problèmes RÉSOLUS par ce changement
sn query incident -q 'rfc.number=CHG0030001' --format json
sn query problem -q 'rfc.number=CHG0030001' --format json
```

## Calendrier et conflits

```bash
# Changements planifiés aujourd'hui et demain
sn query change_request -q 'active=true^start_date>=javascript:gs.beginningOfToday()^start_date<=javascript:gs.daysAgoEnd(-1)' \
  -f number,short_description,type,state,start_date,end_date,cmdb_ci,assignment_group -o start_date --format json

# Changements d'urgence de la semaine
sn query change_request -q 'type=emergency^opened_at>=javascript:gs.beginningOfThisWeek()' -o -opened_at --format json

# En attente d'approbation
sn query change_request -q 'active=true^approval=requested' -o start_date --format json

# Changements qui chevauchent une fenêtre sur un CI
# (début avant la fin de la fenêtre ET fin après le début de la fenêtre)
sn query change_request -q "active=true^cmdb_ci.name=SRV-APP-01^start_date<=javascript:gs.dateGenerate('2026-11-20','23:59:59')^end_date>=javascript:gs.dateGenerate('2026-11-20','00:00:00')" \
  -f number,short_description,state,start_date,end_date -o start_date --format json
```

Pour couvrir aussi les CI secondaires, cherchez dans `task_ci` :

```bash
sn query task_ci -q 'ci_item.name=SRV-APP-01^task.sys_class_name=change_request^task.active=true' \
  -f task.number,task.short_description,task.state --format json
```

## Corrélation incident ↔ changement

Question type : « un changement récent a-t-il causé cet incident ? »

1. Lisez l'incident : `cmdb_ci`, `business_service`, `opened_at`.
2. Cherchez les changements réalisés sur ce CI (ou ce service) dans les jours qui
   précèdent l'ouverture :
   ```bash
   sn query change_request -q 'cmdb_ci.name=<CI>^work_end>=javascript:gs.daysAgoStart(7)' \
     -f number,short_description,state,work_start,work_end,close_code,assignment_group -o -work_end --format json
   ```
   Si `work_end` est vide (changement non saisi), utilisez `end_date`.
3. Recoupez avec `task_ci` pour les CI secondaires et avec `business_service`.
4. Présentez la corrélation comme une **hypothèse** (chronologie + CI commun), pas
   comme une cause établie, sauf si `caused_by` est renseigné.

## Statistiques

```bash
# Taux de réussite du mois dernier
sn api /api/now/stats/change_request -p sysparm_count=true -p sysparm_group_by=close_code \
  -p sysparm_display_value=true -p 'sysparm_query=closed_atBETWEENjavascript:gs.beginningOfLastMonth()@javascript:gs.endOfLastMonth()'

# Changements par type sur 90 jours
sn api /api/now/stats/change_request -p sysparm_count=true -p sysparm_group_by=type \
  -p sysparm_display_value=true -p 'sysparm_query=opened_at>=javascript:gs.daysAgoStart(90)'
```

## Pièges

* `change_task` n'a pas de préréglage de champs dans `sn` : passez `-f`.
* `risk` et `close_code` varient beaucoup d'une instance à l'autre ; vérifiez
  dans `sys_choice` (`name=change_request^element=risk`).
* `sysapproval_approver.state` : `requested`, `approved`, `rejected`,
  `not_required`, `cancelled`, `not requested` (valeurs stockées).
* Les dates littérales sont interprétées selon le fuseau du compte ou en UTC :
  préférez `javascript:gs.dateGenerate(...)`.
