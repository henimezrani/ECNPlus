# Déployer gratuitement sur Render et Neon

Le site et son API tournent dans **un seul service web Render Free**. Neon conserve les données des trois espaces, les paramètres des lectures, les points de reprise et les jetons ECNPlus chiffrés. Aucun ordinateur personnel ne doit rester allumé. L’iPad accède au site dans Safari ; il peut aussi l’ajouter à l’écran d’accueil.

## Déploiement actuel

Le service est en ligne sur **https://ecn-companion.onrender.com** dans le workspace Render confirmé par le propriétaire. Il utilise le plan **Free**, la région **Frankfurt**, le dépôt `henimezrani/ECNPlus` et la branche `main`. Les espaces sont accessibles à `/yasmine`, `/houssein` et `/autre`.

Ce service a été créé directement avec l’intégration Render. Sa configuration active et ses sept variables privées sont gérées dans [Render Dashboard](https://dashboard.render.com/web/srv-dbhra4rl550s73dvklc0). Le fichier `render.yaml` est une référence de recréation ; aucun Blueprint ne le synchronise automatiquement avec ce service. Ne pas créer un deuxième service pour terminer cette installation. Le service utilise le contrôle TCP Render par défaut ; son endpoint HTTP `/healthz` a également été vérifié publiquement.

L’historique local et les paramètres de Yasmine ont été importés et comparés au tableau de bord déployé. Les deux autres espaces sont prêts, sans historique. Aucun jeton ECNPlus n’a été importé. Les étapes de préparation ci-dessous servent à une nouvelle installation ou à une recréation ; elles ne sont pas à refaire pour utiliser le site actuel.

## Préparer la configuration privée

Cette étape se fait une seule fois sur l’ordinateur qui possède les données locales. Ne partagez et ne publiez pas les fichiers `.env`, `deploy.env`, `access-codes.json` ou les dossiers de données.

1. Installer les dépendances du mode hébergé dans un environnement Python, puis y exécuter les commandes suivantes :

   ```sh
   python -m pip install -r requirements.txt
   python -m ecnplus.cloud_admin prepare
   ```

2. Le fichier local `.env` doit contenir les deux connexions fournies par Neon pour le même projet, la même branche et la même base :

   - `DATABASE_URL` : connexion **pooled**, dont le nom d’hôte contient `-pooler`.
   - `DATABASE_URL_UNPOOLED` : connexion **directe**, sans `-pooler`. Elle est nécessaire aux migrations et aux verrous de synchronisation.

   Garder `sslmode=require` ou une vérification TLS plus stricte.

3. La commande `prepare` conserve les valeurs déjà présentes et crée les clés manquantes. Elle reprend les codes de `access-codes.json` s’il existe, sinon elle génère trois codes. Elle écrit les **sept variables** nécessaires à Render dans le fichier privé `deploy.env` :

   | Variable | Utilisation |
   |---|---|
   | `DATABASE_URL` | Accès courant à Neon |
   | `DATABASE_URL_UNPOOLED` | Migrations et verrou par espace |
   | `SESSION_SECRET` | Signature des sessions d’accès |
   | `TOKEN_ENCRYPTION_KEY` | Chiffrement des jetons ECNPlus |
   | `ACCESS_CODE_YASMINE` | Code de l’espace Yasmine |
   | `ACCESS_CODE_HOUSSEIN` | Code de l’espace Houssein |
   | `ACCESS_CODE_AUTRE` | Code de l’espace Autre |

   Conserver une copie privée de ces valeurs. **Ne pas régénérer `TOKEN_ENCRYPTION_KEY` après avoir enregistré des jetons** : une autre clé ne peut pas les déchiffrer. Si cette clé est perdue, les historiques restent disponibles, mais chaque personne doit enregistrer à nouveau son jeton. Changer un code d’accès invalide les sessions de cet espace.

## Importer l’historique local existant (facultatif)

Les nouvelles personnes commencent avec un espace vide et peuvent simplement synchroniser. Pour conserver un historique déjà collecté, avant la première synchronisation en ligne :

```sh
python -m ecnplus.cloud_admin migrate
python -m ecnplus.cloud_admin import-local --profile yasmine --data-dir data --settings readings.json
```

Pour importer des données provenant du mode local à trois espaces, remplacer les deux chemins par ceux du profil concerné. Sur Windows, ils se trouvent sous `%LOCALAPPDATA%\ECN Companion\profiles\yasmine\`. L’import refuse un espace qui contient déjà un historique. Il ne copie **aucun jeton ECNPlus**. Il conserve les dates de lectures et les tentatives observées, y compris les tentatives anciennes absentes des dernières réponses ECNPlus.

## Créer le service Render

Le dépôt contient `render.yaml`, qui décrit un seul service **Free**, en région **Frankfurt**, sans disque ni base Render payants.

1. Dans [Render](https://dashboard.render.com/), choisir **New → Blueprint**.
2. Sélectionner le dépôt GitHub **henimezrani/ECNPlus**, branche **main**. Le chemin du Blueprint est `render.yaml`.
3. Render demande les sept variables marquées `sync: false`. Copier leurs valeurs depuis le fichier privé `deploy.env`, sans guillemets JSON autour de la valeur si vous les collez une par une. Conserver exactement la valeur de la clé de chiffrement, y compris son éventuel `=` final.
4. Vérifier le plan **Free**, la région **Frankfurt** et l’absence de service payant, puis lancer le déploiement.
5. Attendre que le service soit **Live**, puis ouvrir son adresse `https://…onrender.com`.

Si l’interface Blueprint n’est pas disponible, créer **New → Web Service**, sélectionner le même dépôt et la même branche, puis saisir :

| Champ | Valeur |
|---|---|
| Language / Runtime | Python |
| Region | Frankfurt |
| Instance type | Free |
| Build Command | `pip install -r requirements.txt` |
| Start Command | `python -m ecnplus.cloud_admin migrate && gunicorn --workers 1 --threads 8 --timeout 120 --bind 0.0.0.0:$PORT wsgi:app` |
| Health Check Path | `/healthz` |

Ajouter les sept variables dans **Environment**. L’option **Add from .env** peut importer le contenu de `deploy.env` ; vérifier que les valeurs sont importées sans leurs guillemets de sérialisation. Ne pas importer le `.env` complet du projet : il contient aussi la configuration du collecteur local.

L’application utilise automatiquement `RENDER_EXTERNAL_URL`, fourni par Render. **Aucune URL n’a besoin d’être codée dans le dépôt.** Pour un domaine personnalisé, ajouter `PUBLIC_BASE_URL=https://votre-domaine` et utiliser exclusivement cette adresse.

## Première utilisation

- Ouvrir `/yasmine`, `/houssein` ou `/autre` et saisir le code de cet espace.
- Ouvrir **Paramètres**, coller le jeton ECNPlus de cette personne et l’enregistrer. Le jeton peut être fourni avec ou sans le préfixe `Bearer`.
- Choisir **Synchroniser**. Laisser la page ouverte pendant cette mise à jour pour suivre son avancement.
- Consulter ensuite les cours sur ordinateur ou iPad avec la même adresse et le même code. Aucun Python, script Windows ou jeton n’a besoin d’être installé sur l’iPad.
- Si ECNPlus expire un jeton, le remplacer dans les paramètres de cet espace et reprendre la synchronisation. Le site ne peut pas renouveler ce jeton automatiquement.

Les codes sont vérifiés côté serveur ; ils ne figurent pas dans le JavaScript. Le navigateur conserve une session signée, HttpOnly, Secure et limitée à l’espace concerné. Elle expire au plus tard après 24 heures et peut disparaître à la fermeture du navigateur. **Verrouiller mon espace** la ferme immédiatement. Il n’y a ni création de compte, ni adresse e-mail, ni réinitialisation de mot de passe.

## Persistance et interruptions

Les fichiers du serveur Render peuvent disparaître au redémarrage : aucune donnée utilisateur n’y est conservée. Chaque réponse ECNPlus est enregistrée dans Neon. Une seule mise à jour peut écrire dans un espace à la fois ; les autres espaces restent indépendants.

Après une interruption, le site détecte la fin de la tâche lorsque son verrou est libéré et son délai de présence expire (environ deux minutes). Choisir **Reprendre la synchronisation** conserve les réponses réussies et récupère celles qui manquent. Le dernier tableau de bord complet reste visible pendant une nouvelle collecte ou après son échec.

Les anciennes réponses brutes sont supprimées uniquement après la publication atomique d’une collecte complète. Le jeu de données normalisé conserve toutes les tentatives déjà observées, même si ECNPlus cesse de les retourner. Le dernier jeu complet et le dernier essai sont conservés. Modifier les limites des lectures recalcule ces données sans nouvelle collecte ECNPlus.

## Limites de l’hébergement gratuit

[Render Free](https://render.com/docs/free) met le service en veille après 15 minutes sans requête entrante. Le premier accès peut prendre environ une minute. Une synchronisation en cours peut donc être interrompue si personne ne garde la page ouverte ; ses points de reprise restent dans Neon. Les quotas gratuits Render et Neon restent applicables. Cette configuration n’active aucun service payant et n’ajoute pas de surveillance artificielle pour empêcher la veille.

Le système ne garantit pas un historique ECNPlus antérieur à ce qui a été retourné par son API ou importé depuis les collectes locales. Les règles de comptage existantes restent inchangées : date de début, maximum de réponses pour une série dans une lecture, et réponses supérieures à la taille actuelle conservées.

## Diagnostic

- **Échec avant le démarrage** : vérifier les sept variables et que la connexion directe n’utilise pas `-pooler`. Ne pas publier leurs valeurs dans les logs ou une capture.
- **Adresse du site invalide** : utiliser l’URL Render automatique ou le domaine exact configuré dans `PUBLIC_BASE_URL`.
- **Jeton illisible** : vérifier que `TOKEN_ENCRYPTION_KEY` est celle utilisée lors de l’enregistrement ; sinon enregistrer à nouveau le jeton ECNPlus.
- **Jeton d’un autre compte** : choisir l’espace de la bonne personne. Le site refuse de mélanger deux comptes ECNPlus dans un même historique.
- **Mise à jour interrompue** : attendre environ deux minutes puis recharger et reprendre. Si le serveur est encore en veille, attendre sa remise en route.

Le mode local reste disponible avec `python -m ecnplus serve` ; ses données sont distinctes des données Neon. Les commandes locales `sync` et `rebuild` ne modifient pas les espaces hébergés.
