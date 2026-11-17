# Rétro-ingénierie : comment le CLI officiel de ServiceNow s'authentifie

Ce document résume l'analyse faite avec **REA** (Reverse Engineer Anything,
`rea-agents` 6.9.0) pour concevoir l'authentification de `sn`. Il distingue ce
qui a été **observé** (dans le code livré), ce qui est **déduit**, et ce qui
reste **inconnu**.

## Cible

| | |
|---|---|
| Paquet | `@servicenow/sdk-cli` 4.14.2 (dépendance de `@servicenow/sdk`, binaire `now-sdk`), licence MIT |
| Complément | `@servicenow/sdk-api` 4.14.2 (`dist/credentials.js`) |
| Empreinte de l'arbre analysé | `sha256:ff5b4401b8595bd33ff73f867c65f5151a7e3a71bb6926a16af1aaec710acf75` |
| Outil REA | `analyze_javascript_application` (CLI, analyse statique : le code n'est jamais exécuté) |
| Evidence REA (complète) | `ev_6bd531ed2eb57af9d1e91c4a2f3efd115d346dfa6b02628cb73f69dc1a9ed36a` |
| Evidence REA (vue résumé) | `ev_0248293cfe7d8f2c39db7d31eae83589ad0cef4c8dfaf03ecd5da38705084574` |

Couverture : 135 fichiers JS analysés sur 135 sans erreur de parsing, 1 569 findings,
12 constructions de requêtes `fetch`, 58 sources de configuration. Le graphe sémantique
reste partiel (20 153 inconnues, surtout du flux de données dynamique), ce qui est normal
pour une analyse statique.

Je n'avais pas accès à votre instance. Le paquet public du SDK est l'artefact livré
par ServiceNow qui encode le mieux « comment un outil en ligne de commande est censé
se connecter à une instance ».

## Ce que REA a fait ressortir

Les sources de configuration (`reads-environment`) ont révélé trois modes
d'authentification, dont un mode **session pré-authentifiée** que la documentation
publique décrit à peine :

```
SN_SDK_AUTH_TYPE             (défaut "basic")
SN_SDK_INSTANCE_URL
SN_SDK_USER / SN_SDK_USER_PWD
SN_SDK_OAUTH_CLIENT_ID / SN_SDK_OAUTH_CLIENT_SECRET
SN_SDK_SESSION_COOKIE        <- cookies d'une session navigateur
SN_SDK_SESSION_TOKEN         <- X-UserToken de cette session
SN_SDK_SESSION_BEARER_TOKEN  <- jeton OAuth déjà obtenu
SN_SDK_CREDENTIALS_DIR       (stockage par défaut : "keyring")
```

J'ai ensuite lu les modules désignés par ces findings.

### 1. Session UI (cookies + X-UserToken) — `dist/auth/basic-auth/UISession.js`

Observé :
* Connexion : `POST /angular.do?sysparm_type=view_form.login` avec `user_name` / `user_password`
  (inutilisable derrière un SSO, d'où le mode navigateur de `sn`).
* MFA : `GET /validate_multifactor_auth_code.do` puis `POST /validate_mfa_code.do`.
* **Obtention du jeton CSRF sans analyser le HTML** : `POST /angular.do?sysparm_type=get_user`
  avec les cookies. L'instance répond (généralement par un 401) et renvoie le jeton
  dans l'en-tête `X-UserToken-Response`. Une valeur vide est acceptée (ancien modèle
  de sécurité).
* `sdk-api/dist/credentials.js` : pour une session, chaque requête envoie
  `Cookie: …` **et** `X-UserToken: <jeton>`. Pour OAuth : `Authorization: Bearer …`.

Utilisé dans `sn` : `src/sncli/auth/session.py` (`fetch_user_token`) et
`src/sncli/client.py` (`_auth_headers`). Le mode navigateur lit d'abord
`window.g_ck` dans la page, puis se rabat sur `angular.do`.

### 2. OAuth authorization code + PKCE — `dist/auth/OAuth/CodeGrant.js`

Observé :
* `authorization_endpoint = {instance}/oauth_auth.do`, `token_endpoint = {instance}/oauth_token.do`
* client public : `token_endpoint_auth_method: 'none'`, PKCE `S256`, `scope: ''`
* `redirect_uri = '/sdk-oauth.do'` : **un chemin sur l'instance**. L'instance affiche
  le code, que l'utilisateur colle dans le terminal (pas de serveur local).
* `client_id` par défaut `543e5655f77746a28228c6009a599dfb`, remplaçable par `SDK_OAUTH_APP_CLIENT_ID`.
* Rafraîchissement (`dist/auth/OAuth/index.js`) : `refresh_token` quand il reste moins de 15 min.

Utilisé dans `sn` : `src/sncli/auth/oauth.py`. Deux styles de redirection
(loopback `http://localhost:8765/callback` ou hors bande `/…`), et un preset
`--sdk-client` qui reproduit exactement la configuration du SDK.

### 3. Stockage des secrets — `dist/auth/credential-store/*`, `dist/auth/keychain`

Observé : trousseau système par défaut, magasin fichier chiffré en repli.
`sn` utilise le trousseau s'il est disponible (extra `keyring`), sinon un fichier
`0600` dans un dossier `0700`. Sur WSL, c'est en pratique le fichier.

## Déductions

* Le mode session du SDK existe précisément pour les environnements où le mot de
  passe local est désactivé (SSO). Récupérer les cookies d'une session navigateur
  et en déduire le X-UserToken est donc un modèle prévu par ServiceNow, et non un
  contournement.
* Une session cookie a les mêmes droits (ACL) que l'utilisateur dans l'interface :
  `sn` ne peut rien lire de plus que ce que vous voyez déjà.

## Inconnues (à vérifier sur votre instance)

* **Le client OAuth du SDK (`543e5655…`) est-il enregistré sur votre instance ?**
  L'analyse statique ne peut pas le dire. S'il ne l'est pas, `sn login --sdk-client`
  échouera avec `invalid client`. Demandez à votre admin un client OAuth dédié
  (voir le README). C'est de toute façon la voie à privilégier vis-à-vis des
  politiques de sécurité.
* La durée de session (`glide.ui.session_timeout`) et la persistance des cookies de
  votre IdP déterminent si le renouvellement silencieux fonctionne.
* Les pages non classiques (Next Experience / Workspace) n'exposent pas toujours
  `window.g_ck`. Le repli `angular.do` couvre ce cas d'après le code du SDK, mais
  il n'a pas été vérifié sur votre version d'instance.

## Reproduire l'analyse

```bash
npm pack @servicenow/sdk-cli@4.14.2 && mkdir t && tar xzf servicenow-sdk-cli-4.14.2.tgz -C t
npx -y rea-agents@6.9.0 analyze-javascript-application "$PWD/t/package" \
  --detail summary --evidence_output "$PWD/evidence.json" --json
```
