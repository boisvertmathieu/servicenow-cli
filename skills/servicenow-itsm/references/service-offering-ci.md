# Offres de service et CI (`service_offering`, `cmdb_rel_ci`, `svc_ci_assoc`)

Question type : « L'offre de service *X* est-elle liée au CI *Y* ? » (et
« par quel chemin ? »). Réponse en **une commande** : `sn ci-link`.

## Modèle (CSDM)

```text
Service métier / technique     cmdb_ci_service_business, cmdb_ci_service_technical
   │  service_offering.parent  (champ de l'offre, pas une relation CMDB)
   ▼
Offre de service               service_offering   (étend cmdb_ci_service, donc cmdb_ci)
   │  cmdb_rel_ci  « Depends on::Used by »
   ▼
Service applicatif             cmdb_ci_service_auto, _discovered, _calculated, _tags,
   │                           cmdb_ci_query_based_service (groupe CI dynamique)
   │  cmdb_rel_ci « Depends on », et/ou carte de service : svc_ci_assoc (service → chaque CI)
   ▼
Application, base, cluster     cmdb_ci_appl, cmdb_ci_db_instance, cmdb_ci_cluster...
   │  cmdb_rel_ci « Runs on::Runs », « Members::Member of », « Hosted on::Hosts »...
   ▼
Serveur, VM, matériel          cmdb_ci_server (cmdb_ci_win_server, cmdb_ci_linux_server), cmdb_ci_vm_instance...
```

| Table | Rôle | Champs |
|---|---|---|
| `service_offering` | Offre de service | `name`, `parent` (service), `service_classification`, `operational_status`, `owned_by`, `support_group` |
| `cmdb_ci` | Tous les CI (classe dans `sys_class_name`) | `name`, `sys_class_name`, `operational_status`, `install_status` |
| `cmdb_rel_ci` | Relation entre deux CI | `parent`, `child`, `type` (`cmdb_rel_type`, libellé « Parent descr::Child descr ») |
| `svc_ci_assoc` | Contenu d'une carte de service (services cartographiés, par tags, dynamiques) | `service_id`, `ci_id` |

Une relation se lit `parent --type--> child` : « Exchange Prod *Depends on*
Exchange 2019 » ; dans l'autre sens, Exchange 2019 est *Used by* Exchange Prod.

## `sn ci-link` : la commande à utiliser

```bash
sn ci-link "<offre>" "<CI>" --format json
```

* `<offre>` et `<CI>` : nom exact, partie **unique** du nom, ou `sys_id`.
* Ce que la commande cherche, dans cet ordre :
  1. même fiche (`same-record`) ;
  2. le CI est le service parent de l'offre (`offering-parent`, champ
     `service_offering.parent`) ;
  3. le plus court chemin de relations `cmdb_rel_ci` de l'offre vers le CI
     (`direct` = une seule relation, sinon `indirect`), en passant aussi par les
     cartes de service (`svc_ci_assoc` : un service applicatif « contient » le CI).
* Algorithme : recherche en largeur **bidirectionnelle**, vers le bas depuis
  l'offre (parent → enfant) et vers le haut depuis le CI (enfant → parent + les
  services dont la carte contient le CI), une requête groupée (`parentIN…`,
  `childIN…`, `ci_idIN…`) par niveau. Quelques requêtes suffisent, même pour une
  offre qui dépend de centaines de CI. Ne refaites pas ce parcours à la main avec
  `sn query`.

| Option | Défaut | Usage |
|---|---|---|
| `--ci-table TABLE` | `cmdb_ci` | Classe du CI pour lever une ambiguïté de nom : `cmdb_ci_server`, `cmdb_ci_appl`, `cmdb_ci_db_instance`... |
| `--offering-table TABLE` | `service_offering` | `cmdb_ci_service` si l'utilisateur parle d'un service (métier, applicatif) plutôt que d'une offre. |
| `-d`, `--depth N` | `6` | Nombre maximal de relations entre l'offre et le CI (1 à 12). |
| `--direction down\|any` | `down` | `down` : le CI doit être **en aval** de l'offre (modèle CSDM). `any` : relations suivies dans les deux sens. |
| `--exclude-type TEXTE` | aucun | Ignore les types de relation contenant ce texte (`"Connects to"`, `"Members"`...). Répétable. |
| `--no-service-map` | désactivé | N'utilise pas `svc_ci_assoc`. |
| `--max-nodes N` | `5000` | Arrêt de sécurité : nombre de CI atteints par côté. |

### Lire le JSON

```json
{
  "linked": true,
  "link": "indirect",
  "depth": 3,
  "chain": ["Email - Standard", "Exchange Prod", "Exchange 2019", "SRV-MAIL-01"],
  "steps": [
    {"parent": {"name": "Email - Standard", "sys_class_name": "service_offering", "sys_id": "…"},
     "type": "Depends on::Used by",
     "child": {"name": "Exchange Prod", "sys_class_name": "cmdb_ci_service_auto", "sys_id": "…"},
     "source": "cmdb_rel_ci", "rel_sys_id": "…"},
    "…"
  ],
  "offering": {"name": "Email - Standard", "parent": "Email", "operational_status": "Operational", "_url": "…"},
  "ci": {"name": "SRV-MAIL-01", "sys_class_name": "cmdb_ci_win_server", "_url": "…"},
  "search": {"direction": "down", "max_depth": 6, "queries": 4, "truncated": false,
             "exhausted": false, "reached_from_offering": 12, "reached_from_ci": 3,
             "service_map": true, "warnings": []}
}
```

* `linked` : la réponse. `chain` : le chemin à citer. `steps[].source` :
  `cmdb_rel_ci` (relation), `svc_ci_assoc` (carte de service) ou
  `service_offering.parent`.
* `steps[].type` dit la **nature** du lien : un lien
  `Depends on` / `Runs on` / carte de service est un vrai lien de dépendance ; un
  lien qui passe par `Connects to` (réseau) ou `Members` peut être fortuit.
* Le JSON est toujours produit (code de sortie `0`), que le lien existe ou non.
  Code `1` : offre ou CI introuvable, **nom ambigu** (le message liste les
  candidats avec leur classe et leur `sys_id` : relancez avec le `sys_id` ou
  `--ci-table`), session expirée.

### Interpréter un « non lié »

Regardez `search` avant de conclure :

| Indice | Signification | Suite |
|---|---|---|
| `exhausted: true`, `truncated: false` | Tout le graphe accessible a été parcouru : **aucun lien en aval** de l'offre. | Réponse fiable « non lié » (avec les droits de l'utilisateur). Option : `--direction any`. |
| `exhausted: false` | Profondeur atteinte. | Relancez avec `--depth 10`. |
| `truncated: true` | Trop de CI (graphe très connecté, souvent via `Connects to`). | `--exclude-type "Connects to"` puis relancez. |
| `service_map: false` + avertissement | `svc_ci_assoc` illisible (ACL) : les cartes de service n'ont pas été suivies. | Signalez-le ; le lien peut exister via une carte. |
| `reached_from_offering: 0` | L'offre n'a aucune relation sortante. | Offre non modélisée dans la CMDB ; vérifiez `--direction any` et l'indice « tickets » ci-dessous. |

`--direction any` trouve aussi les liens modélisés « à l'envers » (CI parent de
l'offre) ou latéraux, mais aussi des chemins sans valeur (offre → service métier
→ autre offre → CI). Présentez un lien trouvé uniquement en `any` comme une
**hypothèse** et montrez le chemin.

## Requêtes complémentaires

```bash
# Ce dont l'offre dépend directement (1 niveau)
sn query cmdb_rel_ci -q 'parent.name=<offre>' -f type,child,child.sys_class_name -n 100 --format json

# Ce qui dépend directement d'un CI (1 niveau vers le haut)
sn query cmdb_rel_ci -q 'child.name=<CI>' -f parent,parent.sys_class_name,type -n 100 --format json

# Services applicatifs dont la carte contient le CI
sn query svc_ci_assoc -q 'ci_id.name=<CI>' -f service_id,service_id.sys_class_name -n 100 --format json

# Offres d'un service métier
sn query service_offering -q 'parent.name=<service>' -f name,operational_status,support_group -n 100 --format json

# Indice d'usage (pas un lien CMDB) : incidents qui portent à la fois l'offre et le CI
sn api /api/now/stats/incident -p sysparm_count=true \
  -p 'sysparm_query=service_offering.name=<offre>^cmdb_ci.name=<CI>'
```

`sn query` sur `cmdb_rel_ci` n'affiche que les relations **directes** : pour
« est-ce lié, même indirectement ? », utilisez toujours `sn ci-link`.

## Pièges

* Les noms de CI ne sont pas uniques (serveur et application homonymes, CI
  retirés) : en cas d'ambiguïté, `sn ci-link` refuse de deviner. Choisissez le
  candidat avec l'utilisateur ou par sa classe.
* Un CI retiré (`install_status=7`) ou non opérationnel peut encore porter des
  relations : le JSON donne `operational_status` de l'offre et du CI ; signalez
  un CI non opérationnel.
* Sans le module CSDM, `service_offering` peut être absente (erreur
  « Invalid table ») : utilisez `--offering-table cmdb_ci_service`.
* Les droits de lecture sur `cmdb_rel_ci` et `svc_ci_assoc` peuvent être limités :
  « non lié » veut alors dire « aucun lien visible avec vos droits ».
