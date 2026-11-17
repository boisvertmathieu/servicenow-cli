# Enchaînements d'analyse (INC, CHG, PRB, KB)

Recettes pour les demandes qui traversent plusieurs tables. Lancez en parallèle
les étapes indépendantes. Remplacez les valeurs entre `<...>`.

## 1. « Que se passe-t-il sur INC0012345 ? »

1. En parallèle :
   ```bash
   sn get INC0012345 -f number,short_description,description,state,priority,caller_id,assignment_group,assigned_to,cmdb_ci,business_service,opened_at,resolved_at,close_code,close_notes,problem_id,rfc,caused_by,parent_incident,reassignment_count --format json
   sn get INC0012345 -f work_notes,comments --format json
   ```
2. Si `problem_id` est renseigné : lisez le problème (`workaround`, `cause_notes`,
   `known_error`).
3. Si `caused_by` ou `rfc` est renseigné : lisez le changement (état, fenêtre,
   `close_code`).
4. Restituez : état actuel, qui s'en occupe, chronologie résumée (ouverture,
   réassignations, actions, résolution), liens vers PRB / CHG, prochaine étape
   attendue.

## 2. « Comment résoudre cet incident ? » (aide à la résolution)

1. Lisez l'incident (étape 1 ci-dessus) et extrayez 2 à 4 mots-clés
   discriminants : message d'erreur, application, composant.
2. En parallèle :
   ```bash
   # Erreur connue
   sn search "<mots-clés>" -t problem -q 'known_error=true' -f number,short_description,workaround,state --format json
   # Articles KB
   sn kb "<mots-clés>" -n 5 --format json
   # Incidents similaires résolus
   sn search "<mots-clés>" -t incident -q 'stateIN6,7' -n 10 -f number,short_description,close_notes,resolved_at,cmdb_ci --format json
   # Changement récent sur le même CI (si cmdb_ci connu)
   sn query change_request -q 'cmdb_ci.name=<CI>^end_date>=javascript:gs.daysAgoStart(7)' -f number,short_description,state,start_date,end_date,close_code -o -end_date --format json
   ```
3. Lisez le corps des 1 ou 2 articles KB les plus pertinents (voir
   [knowledge.md](knowledge.md) pour l'HTML et les versions).
4. Proposez, dans cet ordre : contournement d'une erreur connue, procédure KB
   publiée, solution récurrente des incidents similaires (citez leurs numéros),
   piste « changement récent ». Indiquez la source de chaque proposition.

## 3. Revue d'une file (groupe ou personne)

```bash
# Incidents ouverts du groupe, avec indicateurs de risque
sn query incident -q 'active=true^assignment_group.name=<Groupe>' \
  -f number,short_description,state,priority,assigned_to,opened_at,sys_updated_on,reassignment_count,sla_due -o priority -n 100 --format json

# Volume par état et priorité
sn api /api/now/stats/incident -p sysparm_count=true -p sysparm_group_by=state,priority \
  -p sysparm_display_value=true -p 'sysparm_query=active=true^assignment_group.name=<Groupe>'

# SLA dépassés
sn query task_sla -q 'task.active=true^task.assignment_group.name=<Groupe>^has_breached=true^task.sys_class_name=incident' \
  -f task.number,task.short_description,sla,planned_end_time -n 50 --format json
```

Signalez : P1/P2, SLA dépassés ou proches, non assignés, sans mise à jour depuis
plusieurs jours (`sys_updated_on`), forte réassignation (`reassignment_count >= 3`).

## 4. Préparer un CAB / revue des changements

```bash
# Changements à approuver ou planifiés sur les 7 prochains jours
sn query change_request -q 'active=true^start_date>=javascript:gs.beginningOfToday()^start_date<=javascript:gs.daysAgoEnd(-7)' \
  -f number,short_description,type,state,approval,risk,start_date,end_date,cmdb_ci,assignment_group -o start_date -n 100 --format json
```

Pour chaque changement à risque élevé ou d'urgence :

1. Plans : `sn get <CHG> -f justification,implementation_plan,backout_plan,test_plan --format json`
   (signalez un plan de retour arrière vide).
2. Approbations : `sn query sysapproval_approver -q 'sysapproval.number=<CHG>' -f approver,state --format json`.
3. Conflits : autres changements sur le même `cmdb_ci` dont la fenêtre chevauche
   (voir [change.md](change.md)).
4. Historique du CI : incidents des 30 derniers jours sur le `cmdb_ci`, et
   changements précédents `unsuccessful`.

## 5. Bilan post-changement

```bash
sn get <CHG> -f number,state,work_start,work_end,close_code,close_notes,cmdb_ci --format json
sn query incident -q 'caused_by.number=<CHG>' -f number,short_description,priority,state,opened_at --format json
sn query incident -q 'cmdb_ci.name=<CI>^opened_at>=<work_start>' -f number,short_description,priority,opened_at -o opened_at --format json
```

La dernière requête (incidents ouverts sur le CI après le début des travaux)
remonte des corrélations que `caused_by` n'a pas capturées : présentez-les comme
des hypothèses. `<work_start>` s'écrit
`javascript:gs.dateGenerate('AAAA-MM-JJ','HH:MM:SS')`.

## 6. Analyse de problème

1. Lisez le problème et son journal ([problem.md](problem.md)).
2. Incidents rattachés : volume (`/api/now/stats/incident` avec
   `problem_id.number=<PRB>`), puis les plus récents et leurs `close_notes`.
3. Répartition des incidents rattachés par CI ou par mois :
   ```bash
   sn api /api/now/stats/incident -p sysparm_count=true -p sysparm_group_by=cmdb_ci \
     -p sysparm_display_value=true -p 'sysparm_query=problem_id.number=<PRB>'
   ```
4. Changements liés : `rfc` du problème, et changements sur le même CI.
5. KB : article d'erreur connue existant (`source.number=<PRB>` ou recherche par
   mots-clés). S'il n'existe pas alors que `known_error=true` et `workaround` est
   renseigné, signalez-le comme manque.

## 7. « Y a-t-il de la documentation sur X ? »

1. `sn kb "<X>" -n 5 --format json` et, en parallèle,
   `sn search "<X>" -t kb_knowledge -q 'workflow_state=published' -n 10 -f number,short_description,kb_knowledge_base,valid_to --format json`.
2. Rien de pertinent : reformulez (synonymes, termes anglais / français, nom
   technique) et relancez une fois.
3. Toujours rien : cherchez une erreur connue (`-t problem -q 'known_error=true'`)
   et des incidents résolus ; dites clairement qu'aucun article n'a été trouvé
   (avec les droits de l'utilisateur).

## 8. Tableau de bord rapide

```bash
for t in incident problem change_request; do
  echo "== $t"; sn api /api/now/stats/$t -p sysparm_count=true -p sysparm_group_by=priority \
    -p sysparm_display_value=true -p 'sysparm_query=active=true'
done
sn api /api/now/stats/kb_knowledge -p sysparm_count=true -p 'sysparm_query=workflow_state=published'
```

Pour les tendances, comparez deux périodes avec deux requêtes
(`opened_at>=javascript:gs.daysAgoStart(30)` et
`opened_atBETWEENjavascript:gs.daysAgoStart(60)@javascript:gs.daysAgoEnd(31)`).
