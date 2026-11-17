# servicenow-cli (`sn`)

Faire des recherches dans l'instance ServiceNow de votre organisation depuis un
terminal Linux (pensé pour **Ubuntu sous WSL**), y compris quand la connexion passe
par un **SSO d'entreprise** (SAML / OIDC, MFA).

```text
$ sn search vpn
incident (2)
┏━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━┳━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━┓
┃ number     ┃ short_description             ┃ state       ┃ priority     ┃ assignment_group ┃
┡━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━╇━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━┩
│ INC0010001 │ VPN outage in Montreal office │ In Progress │ 1 - Critical │ Network          │
│ INC0010003 │ Slow VPN when on wifi         │ New         │ 3 - Moderate │ Network          │
└────────────┴───────────────────────────────┴─────────────┴──────────────┴──────────────────┘
kb_knowledge (1)
...
```

Le fonctionnement de l'authentification a été établi par rétro-ingénierie du CLI
officiel de ServiceNow (`@servicenow/sdk-cli`) avec REA. Voir
[docs/reverse-engineering.md](docs/reverse-engineering.md).

## Installation (Ubuntu / WSL)

Avec [uv](https://docs.astral.sh/uv/), qui fournit aussi Python au besoin :

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh     # si uv n'est pas déjà installé
uv tool install "servicenow-cli[browser] @ git+https://github.com/boisvertmathieu/servicenow-cli.git"
uv tool update-shell                                 # ajoute ~/.local/bin au PATH, puis rouvrez le terminal
# Navigateur utilisé pour la connexion SSO (installe aussi les bibliothèques système) :
"$(uv tool dir)/servicenow-cli/bin/playwright" install --with-deps chromium
```

Mise à jour : `uv tool upgrade servicenow-cli`. Désinstallation : `uv tool uninstall servicenow-cli`.

Pour essayer sans rien installer de façon permanente :

```bash
uvx --from "servicenow-cli[browser] @ git+https://github.com/boisvertmathieu/servicenow-cli.git" sn --help
```

> La fenêtre de connexion s'affiche via **WSLg** (Windows 11, ou Windows 10 récent
> avec WSLg). Testez avec `sudo apt install x11-apps && xeyes`. Sans WSLg, utilisez
> `--method oauth` ou `--method cookie` (voir plus bas).

## Démarrage

```bash
sn configure --instance moncompagnie            # => https://moncompagnie.service-now.com
sn login                                         # fenêtre de navigateur -> votre SSO -> MFA
sn whoami
```

Si votre instance propose plusieurs IdP ou une page de choix, indiquez le point
d'entrée SSO :

```bash
sn configure --sso-login-path "login_with_sso.do?glide_sso_id=<sys_id_de_la_config_SSO>"
```

## Rechercher

```bash
sn search "imprimante 3e étage"                      # incidents, RITM, changements, problèmes, KB
sn search vpn -t incident -q "active=true^priority<=2" -n 20
sn search "contrat" -t sc_req_item,sc_task --url     # avec un lien vers chaque fiche
sn kb "configurer le VPN"                            # articles de connaissance
sn get INC0012345                                    # table déduite du préfixe
sn get change_request 3f2c...                        # par sys_id
sn query incident -q "assignment_group.name=Service Desk^active=true" -o -sys_updated_on
sn open RITM0012345                                  # ouvre la fiche dans le navigateur Windows
sn api /api/now/table/sys_user_group -p sysparm_limit=5   # n'importe quel endpoint REST
```

* `--format json|csv` sur toutes les commandes de lecture (pratique avec `jq`).
* `sn search` utilise la recherche par mots-clés de ServiceNow (`123TEXTQUERY321`).
  Pour une table non indexée, il bascule automatiquement sur `LIKE`
  (short_description / description). `--like` force ce mode.
* Tables par défaut : `sn configure --tables incident,sc_req_item,kb_knowledge`.
* Les requêtes encodées se copient depuis une liste ServiceNow : clic droit sur le
  fil d'Ariane du filtre, puis *Copy query*.

## Méthodes de connexion

| Méthode | Commande | Admin requis | Quand l'utiliser |
|---|---|---|---|
| **Navigateur SSO** (défaut) | `sn login` | non | Cas général. Vous vous connectez comme d'habitude ; `sn` récupère les cookies de session et le jeton `X-UserToken`. |
| **OAuth + PKCE** | `sn login -m oauth` | oui (un client OAuth) | Le plus propre : jetons rafraîchissables, révocables par l'admin, et aucun cookie de navigateur stocké. |
| **Cookie collé** | `sn login -m cookie` | non | Pas de WSLg, ou dépannage : collez l'en-tête `Cookie` copié depuis les DevTools de votre navigateur Windows. |

### Navigateur SSO : détails

* Un profil de navigateur dédié est conservé (`~/.local/state/sncli/browser-profile/`).
  Si votre IdP garde la session (« Rester connecté »), une session ServiceNow expirée
  est **renouvelée automatiquement et sans fenêtre** à la prochaine commande. Sinon,
  `sn` vous demandera de relancer `sn login`.
* Seuls les cookies du domaine de l'instance sont stockés. Les cookies de l'IdP
  restent dans le profil de navigateur.
* Pour utiliser un Chrome ou un Edge installé dans WSL : `export SN_BROWSER_CHANNEL=chrome`
  (ou `SN_BROWSER_PATH=/chemin/vers/chrome`).

### OAuth : configuration côté admin ServiceNow

*System OAuth > Application Registry > New > Create an OAuth API endpoint for external clients* :
* **Public client** : coché (pas de secret ; PKCE)
* **Redirect URL** : `http://localhost:8765/callback`
* Durées de vie des jetons d'accès et de rafraîchissement : selon votre politique

Puis :

```bash
sn configure --auth-method oauth --oauth-client-id <client_id>
sn login
```

WSL2 transmet `localhost` de Windows vers la distribution : la redirection du
navigateur Windows arrive donc dans le terminal. Si votre admin préfère une
redirection affichée par l'instance (copier-coller du code, comme le SDK
ServiceNow), utilisez `--oauth-redirect-uri /chemin_sur_l_instance.do`.

`sn login --sdk-client` reprend le client OAuth public du SDK ServiceNow (observé
dans le code du SDK). Il ne fonctionne que si ce client est enregistré sur votre
instance, et c'est un client qui ne vous appartient pas : n'utilisez cette option
qu'avec l'accord de votre équipe ServiceNow.

## Automatisation / CI

Sans stocker de secret localement, `sn` accepte aussi une session fournie par
variables d'environnement (même principe que `SN_SDK_SESSION_*` dans le SDK) :

```bash
export SN_INSTANCE=https://moncompagnie.service-now.com
export SN_BEARER_TOKEN=...               # OAuth
# ou
export SN_SESSION_COOKIE='JSESSIONID=...; glide_user_route=...'  SN_USER_TOKEN=...
```

Autres variables : `SN_PROFILE`, `SN_CA_BUNDLE` (AC de l'entreprise, si un proxy
inspecte le TLS), `SN_OAUTH_CLIENT_SECRET` (clients confidentiels uniquement).

## Sécurité

* Les identifiants sont stockés dans le trousseau système s'il y en a un
  (installez avec l'extra `keyring` : `uv tool install --force "servicenow-cli[browser,keyring] @ git+https://github.com/boisvertmathieu/servicenow-cli.git"`), sinon dans
  `~/.local/state/sncli/credentials/<profil>.json` (permissions `0600`).
* `sn logout --forget-browser` efface les jetons **et** le profil de navigateur.
* `sn` n'a que vos droits ServiceNow (mêmes ACL que dans l'interface). Il n'y a
  aucun contournement d'authentification. Vérifiez quand même que l'usage d'un
  client en ligne de commande respecte la politique de votre organisation.

## Développement

```bash
uv sync                                   # crée .venv, installe le projet + le groupe dev (uv.lock)
uv run playwright install chromium
uv run pytest
uv run sn --help                          # lance le CLI depuis les sources
```

Les tests démarrent une **fausse instance ServiceNow et un faux IdP SSO**
(`tests/fake_servicenow.py`). Ils couvrent la connexion SSO dans un vrai Chromium
headless, le renouvellement silencieux après expiration, OAuth PKCE (loopback et
hors bande, rafraîchissement, vérificateur PKCE invalide refusé), le refus des
sessions invité, la rotation des cookies, la recherche avec repli `LIKE`, `get`,
`query`, `kb` et `api`.
