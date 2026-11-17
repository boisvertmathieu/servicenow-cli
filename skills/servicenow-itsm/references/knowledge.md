# Base de connaissances (`KB`, table `kb_knowledge`)

Les articles de connaissance documentent procédures, solutions et erreurs
connues. Ils sont rangés dans des bases (`kb_knowledge_base`) et des catégories
(`kb_category`), et suivent un cycle de publication (`workflow_state`).

## Champs utiles

| Champ | Contenu | Remarque |
|---|---|---|
| `number` | `KB0010001` | partagé par toutes les versions d'un article |
| `short_description` | Titre | |
| `text` | Corps de l'article, **en HTML** | articles de type HTML |
| `wiki` | Corps, pour les articles de type wiki | |
| `article_type` | `text` / `wiki` | |
| `workflow_state` | `draft`, `review`, `published`, `pending_retirement`, `retired`, `outdated` | |
| `kb_knowledge_base` | Base | `kb_knowledge_base.title=IT` |
| `kb_category` | Catégorie | `kb_category.label=VPN` |
| `latest` | `true` sur la dernière version (versionnement actif) | |
| `version` | Version de l'article | |
| `valid_to` | Date d'expiration | |
| `author`, `published`, `sys_updated_on` | Auteur, date de publication, dernière modification | |
| `sys_view_count`, `rating`, `use_count` | Popularité | |
| `meta` | Mots-clés de recherche | |
| `source` | Tâche d'origine (incident, problème) | |
| `cmdb_ci` | CI concerné | |
| `can_read_user_criteria` | Restrictions de lecture | |

## Trouver un article

Deux commandes, à combiner :

```bash
# 1. API Knowledge : pertinence + extrait (snippet). Rapide, articles publiés et lisibles par l'utilisateur.
sn kb "<symptôme ou question>" -n 5 --format json

# 2. Recherche par mots-clés sur la table, avec filtre (base, catégorie, CI...)
sn search "<mots-clés>" -t kb_knowledge -q 'workflow_state=published^kb_knowledge_base.title=IT' \
  -f number,short_description,kb_category,sys_updated_on,valid_to -n 10 --format json
```

* `sn kb` n'accepte ni `-q` ni `-f` : pour filtrer, utilisez `sn search -t kb_knowledge`.
* Le JSON de `sn kb` contient `number`, `short_description`, `snippet`, `score`,
  `sys_id`, `_url` (lien `kb_view.do`, lisible par un humain). En repli sur la
  table, il n'y a ni `snippet` ni `score`.
* Formulez la recherche avec les termes de l'utilisateur **et** avec les termes
  techniques (message d'erreur exact, nom de l'application). Lancez les deux en
  parallèle si le premier essai est maigre.
* Les `_url` de `sn search` / `sn query` sur `kb_knowledge` ouvrent le
  formulaire d'édition ; pour un lien de lecture, donnez
  `<instance>/kb_view.do?sys_kb_id=<sys_id>` (comme `sn kb`).

## Lire un article

```bash
sn query kb_knowledge -q 'number=KB0010001^workflow_state=published' \
  -f number,short_description,text,kb_knowledge_base,kb_category,version,valid_to,sys_updated_on -n 1 --format json
```

* **Versions** : avec le versionnement, plusieurs fiches portent le même numéro.
  `sn get KB0010001` prend la première trouvée, qui peut être une ancienne
  version. Filtrez donc par `workflow_state=published` (ou `latest=true`) avec
  `sn query`, comme ci-dessus.
* `text` est du HTML. Pour le lire, retirez les balises :
  ```bash
  ... --format json | python3 -c 'import json,sys,re,html; [print(html.unescape(re.sub(r"<[^>]+>"," ",r.get("text") or ""))) for r in json.load(sys.stdin)]'
  ```
  Conservez les étapes numérotées et les commandes ; ignorez la mise en forme.
* Si `text` est vide, regardez `wiki` (articles de type wiki).

## Contrôler la qualité d'un article

```bash
# Articles publiés expirés ou bientôt expirés
sn query kb_knowledge -q 'workflow_state=published^valid_to<=javascript:gs.daysAgoEnd(-30)' \
  -f number,short_description,valid_to,author,kb_knowledge_base -o valid_to --format json

# Articles publiés non modifiés depuis un an dans une base
sn query kb_knowledge -q 'workflow_state=published^kb_knowledge_base.title=IT^sys_updated_on<javascript:gs.daysAgoStart(365)' \
  -f number,short_description,sys_updated_on,sys_view_count -o sys_updated_on --format json

# Retours des lecteurs
sn query kb_feedback -q 'article.number=KB0010001' -f rating,useful,comments,sys_created_on -o -sys_created_on --format json
```

Avant de recommander un article, vérifiez `workflow_state=published` et
`valid_to` : signalez un article expiré ou ancien comme tel.

## Liens avec les tâches

```bash
# Articles attachés à un incident (bouton "Attach" du formulaire)
sn query m2m_kb_task -q 'task.number=INC0012345' -f kb_knowledge.number,kb_knowledge.short_description --format json

# Incidents ayant utilisé un article
sn query m2m_kb_task -q 'kb_knowledge.number=KB0010001' -f task.number,task.short_description,task.state -n 50 --format json

# Articles créés à partir d'un incident ou d'un problème
sn query kb_knowledge -q 'source.number=PRB0040001' -f number,short_description,workflow_state --format json
```

## Statistiques

```bash
# Articles publiés par base
sn api /api/now/stats/kb_knowledge -p sysparm_count=true -p sysparm_group_by=kb_knowledge_base \
  -p sysparm_display_value=true -p 'sysparm_query=workflow_state=published'
```

## Pièges

* Les droits de lecture (user criteria) s'appliquent : un article absent pour
  l'utilisateur peut exister pour d'autres. Ne concluez pas « aucun article » sans
  le préciser.
* `kb_knowledge` n'est pas une tâche : pas de `active`, `state` ni `assigned_to`.
* `sn kb` se replie sur la table quand l'API Knowledge est indisponible : son JSON
  n'a alors ni `snippet` ni `score`.
