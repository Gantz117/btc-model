# Modèle de prévision du prix du Bitcoin

Chaque jour, ce modèle calcule automatiquement et gratuitement :

- une **fourchette de prix à 80 %** pour la clôture du jour, du dimanche et de la fin du mois, et une **cible médiane** pour le dimanche et la fin du mois (pas au jour : à cet horizon, aucun signal testé n'a fait mieux que « prix inchangé ») ;
- un **indice de confiance** (0–100) pour chaque cible ;
- un **régime de marché** (accumulation profonde → surchauffe) qui situe le Bitcoin dans son cycle, avec ce qui s'est passé historiquement 3, 6 et 12 mois après chaque régime ;
- le **lundi**, une **synthèse** des informations qui ont servi au calcul (cycles, whales on-chain, sentiment, actualités) ;
- le **1er du mois**, un **bilan** qui compare les prévisions du mois écoulé aux prix réels.

Tout s'affiche sur un tableau de bord web. Le calcul tourne sur les serveurs de GitHub, ton ordinateur n'a pas besoin d'être allumé.

> ⚠️ Outil d'aide à l'analyse, pas un conseil en investissement. Regarde l'onglet « Fiabilité historique » du tableau de bord avant de te fier à une cible.

---

## Installation (environ 15 minutes, sans écrire de code)

### Étape 1 — Créer un compte GitHub
Va sur <https://github.com/signup> et crée un compte gratuit (si tu n'en as pas déjà un).

### Étape 2 — Créer le dépôt
1. Clique sur le **+** en haut à droite → **New repository**.
2. Nom : `btc-model` (ou ce que tu veux).
3. Coche **Public**. *(Indispensable pour que le tableau de bord et les calculs soient gratuits. Conséquence : tes prévisions seront visibles par quiconque trouve l'adresse.)*
4. Coche **Add a README file**, puis **Create repository**.

### Étape 3 — Envoyer les fichiers
1. Décompresse le fichier `btc-model.zip` sur ton ordinateur.
2. Dans ton dépôt GitHub, clique sur **Add file → Upload files**.
3. Ouvre le dossier décompressé, sélectionne **tout son contenu** (les dossiers `.github`, `data`, `docs`, `src` et les fichiers `README.md`, `requirements.txt`) et glisse-le dans la page GitHub.
4. En bas, clique sur **Commit changes**.

**Vérification :** à la racine du dépôt tu dois voir un dossier `.github`. S'il manque (certains navigateurs ignorent les dossiers commençant par un point) :
1. Clique sur **Add file → Create new file**.
2. Dans le nom du fichier, tape exactement : `.github/workflows/daily.yml`
3. Ouvre le fichier `daily.yml` du dossier décompressé (`.github/workflows/`) avec le Bloc-notes, copie tout son contenu et colle-le dans GitHub.
4. **Commit changes**.

### Étape 4 — Autoriser le robot à enregistrer ses résultats
1. Onglet **Settings** du dépôt → menu de gauche **Actions → General**.
2. Tout en bas, section **Workflow permissions** : choisis **Read and write permissions** → **Save**.

### Étape 5 — Activer le tableau de bord
1. **Settings → Pages** (menu de gauche).
2. **Source** : *Deploy from a branch*. **Branch** : `main`, dossier `/docs` → **Save**.
3. Après 1–2 minutes, l'adresse de ton tableau de bord s'affiche en haut de cette page, du type `https://TON-PSEUDO.github.io/btc-model/`. Mets-la en favori.

### Étape 6 — (Facultatif, recommandé) Brancher Gemini pour lire l'actualité
Sans cette étape, les titres sont notés par un simple lexique. Avec, Gemini note chaque titre, résume l'actualité du jour en français et rédige la synthèse du lundi.

> **Ton abonnement Gemini (Google AI Pro) ne suffit pas** : d'après la documentation Google, ses avantages s'appliquent seulement à l'interface web d'AI Studio, pas à l'API utilisée ici. Mais l'API a un **palier gratuit**, largement suffisant (le modèle fait 1 à 2 appels par jour). Contrepartie du palier gratuit : Google peut utiliser les contenus envoyés pour améliorer ses produits. Ici, il ne s'agit que de titres d'actualité publics et de chiffres du modèle, rien de personnel.

1. Va sur <https://aistudio.google.com/apikey> et connecte-toi avec ton compte Google.
2. Clique sur **Create API key** (accepte de créer un projet si on te le propose). **N'active pas la facturation** : sans facturation, il est impossible d'être débité.
3. Copie la clé (elle commence par `AIza…`). Ne la partage avec personne.
4. Dans ton dépôt GitHub : **Settings → Secrets and variables → Actions → New repository secret**.
5. Nom : `GEMINI_API_KEY` — Valeur : colle la clé → **Add secret**.

GitHub garde la clé chiffrée : elle n'apparaît ni dans le code ni sur le tableau de bord, même si le dépôt est public. Dans le tableau de bord, la pastille « actualité : Gemini » confirme que c'est branché (sinon elle affiche « lexique »).

### Étape 7 — Premier lancement
1. Onglet **Actions** → clique sur **Prévision quotidienne** à gauche.
2. Bouton **Run workflow** → **Run workflow**.
3. Le premier lancement prend **5 à 15 minutes** : il télécharge l'historique, fait le backtest et reconstitue les prévisions des deux derniers mois pour que le premier bilan mensuel soit déjà disponible. Une pastille verte ✅ indique que c'est terminé.
4. Recharge ton tableau de bord.

Ensuite tout est automatique : le calcul se lance **chaque jour à 02:35 UTC** (03:35 à Paris en hiver, 04:35 en été).

---

## Que faire si…

| Situation | Solution |
|---|---|
| Pastille rouge ❌ dans Actions | Clique dessus pour voir l'erreur, puis relance avec **Re-run jobs**. Une panne passagère d'une source de données ne bloque pas le calcul (la source est simplement signalée comme indisponible). |
| « données 60 % » dans le tableau de bord | Certaines sources étaient indisponibles ce jour-là ; l'indice de confiance est automatiquement ramené vers 50. |
| GitHub m'envoie un email disant que le workflow est désactivé | GitHub désactive les tâches planifiées d'un dépôt public sans activité pendant 60 jours. Les enregistrements quotidiens du robot devraient compter comme activité, mais si cela arrive : **Actions → Prévision quotidienne → Enable workflow**. |
| La pastille affiche « actualité : lexique » alors que j'ai mis la clé | Survole la pastille pour voir la raison (clé invalide, quota dépassé…). Vérifie que le secret s'appelle exactement `GEMINI_API_KEY`. |
| Je veux refaire le backtest | **Actions → Prévision quotidienne → Run workflow**, coche « Refaire le backtest complet ». Il est de toute façon refait chaque lundi. |

---

## Comment ça marche

**1. Cycles et analyse technique** — multiple de Mayer, écart à la moyenne 200 semaines, loi de puissance, phase du cycle de halving, momentum, RSI, croisement MM50/MM200, drawdown, régime de volatilité.

**2. Whales et on-chain** — flux vers/depuis les exchanges, pics de dépôts, réserves des exchanges, MVRV, adresses actives, hashrate (CoinMetrics Community, gratuit) et, quand l'accès gratuit le permet, BTC détenus par les cohortes de whales (> 10 000 BTC, 1 000–10 000 BTC, 100–1 000 BTC), SOPR et position des détenteurs long terme (BGeometrics, accès gratuit vérifié le 02/11/2026, limité aux ~4 dernières années), ainsi que les **flux nets des ETF spot américains** depuis janvier 2024. Chaque variable passe un **test de causalité de Granger** sur 3 ans, dans les deux sens (la variable prévoit-elle le prix ? le prix prévoit-il la variable ?) ; seules celles qui améliorent significativement la prévision des rendements entrent dans le modèle.

**3. Informations de marché** — Fear & Greed et taux de financement dans le modèle ; titres d'actualité (5 flux RSS) lus chaque jour par Gemini (impact probable, importance, thème, résumé en français), ou par un lexique sans clé API. Le lundi, Gemini rédige une synthèse qui croise l'actualité avec les indicateurs chiffrés. La note d'actualité s'accumule jour après jour ; elle n'entre dans le calcul des cibles qu'après 1 an d'historique **et** si elle passe le test de causalité de Granger. Si Google retire un modèle, le suivant de la liste `GEMINI_MODELS` (dans `src/config.py`) prend le relais.

**Régime de marché** — six indicateurs de valorisation long terme (loi de puissance, Mayer, MVRV, moyenne 200 semaines, drawdown, RSI hebdo), chacun converti en percentile de ses 4 dernières années, moyennés et lissés sur 14 jours. Score de −1 à +1. Sur l'historique, il ne dit rien à 3 mois mais sépare nettement les rendements à 1 an (surchauffe : ~56 % de cas positifs ; accumulation profonde : ~90 %, pire cas −13 %) — sur une poignée d'épisodes seulement.

**Modèle** — régression quantile par gradient boosting (scikit-learn), ré-entraînée chaque jour. Un backtest walk-forward calibre la cible (rapprochée de la prévision naïve « le prix ne bouge pas » quand le modèle ne fait pas mieux), la largeur des intervalles et l'indice de confiance.

**Cible au jour désactivée** — elle reste calculée et enregistrée (pour le bilan mensuel) mais n'est plus affichée. Pour la réactiver : `CIBLE_JOUR = True` dans `src/config.py`.

**Indice de confiance** — probabilité, apprise sur l'historique, que le prix finisse du côté annoncé (hausse/baisse). 50 = pile ou face.

### Sources de données (toutes gratuites, sans clé)
CoinMetrics Community API · Kraken (prix) · Gemini API (facultatif, palier gratuit) · BGeometrics (palier gratuit limité à ~10 requêtes/heure) · alternative.me (Fear & Greed) · CoinDesk, Cointelegraph, Bitcoin Magazine, Decrypt, Google News (RSS).

### Structure
```
src/            code Python (données, variables, causalité, modèle, rapports)
data/           historique initial + caches mis à jour chaque jour
docs/index.html tableau de bord
docs/data/      résultats (prévisions, synthèses, bilans, backtest)
.github/workflows/daily.yml   planification quotidienne
```
Les réglages (horizons, seuil de Granger, séries on-chain, flux RSS) sont dans `src/config.py`.
