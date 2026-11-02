"""Paramètres du modèle. Tout ce qui est réglable est ici."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"            # caches (données brutes)
SEED = DATA / "seed"
OUT = ROOT / "docs" / "data"    # sorties lues par le tableau de bord
for p in (DATA, OUT, OUT / "weekly", OUT / "monthly"):
    p.mkdir(parents=True, exist_ok=True)

GENESIS = "2009-01-03"
HALVINGS = ["2012-11-28", "2016-07-09", "2020-05-11", "2024-04-20", "2028-04-15"]  # 2028 = estimation

# Horizons (en jours) utilisés pour l'entraînement ; la prédiction se fait à l'horizon exact.
TRAIN_HORIZONS = [1, 2, 3, 4, 5, 6, 7, 9, 12, 15, 19, 24, 31]
TRAIN_START = "2013-01-01"
QUANTILES = (0.10, 0.50, 0.90)   # intervalle de confiance à 80 %
# Cible chiffrée au jour : désactivée (le backtest ne bat pas « prix inchangé » à 1 jour). Seule la fourchette est publiée.
CIBLE_JOUR = False

# Causalité de Granger : fenêtre d'analyse et seuil
GRANGER_WINDOW_DAYS = 3 * 365
GRANGER_MAX_LAG = 7
GRANGER_ALPHA = 0.05

# API BGeometrics (palier gratuit : ~10 requêtes/heure). Séries on-chain « whales » et cohortes.
# Si une série est réservée aux abonnés, elle est ignorée automatiquement.
BGEO_SERIES = {
    "coins-addr-10K-BTC": "whales_10k",          # BTC détenus par les adresses > 10 000 BTC
    "coins-addr-10K-1K-BTC": "whales_1k_10k",    # BTC détenus par les adresses 1 000–10 000 BTC
    "coins-addr-1K-100-BTC": "sharks_100_1k",    # BTC détenus par les adresses 100–1 000 BTC
    "sopr": "sopr",
    "lth-net-position-change-btc": "lth_netpos",  # variation de position des détenteurs long terme
    "funding-rate": "funding_rate",              # relevé toutes les 8 h -> moyenne journalière
    "etf-flow-btc": "etf_flow",                  # flux nets des ETF spot US (BTC), jours ouvrés depuis 2024-01-11
}
# Le palier gratuit ne renvoie que les ~4 dernières années : le cache local s'allonge ensuite jour après jour.
BGEO_SUM = {"etf_flow"}  # séries à agréger par somme (les autres par moyenne)

NEWS_FEEDS = [
    "https://cointelegraph.com/rss/tag/bitcoin",
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://bitcoinmagazine.com/feed",
    "https://decrypt.co/feed",
    "https://news.google.com/rss/search?q=bitcoin+price&hl=en-US&gl=US&ceid=US:en",
]

# Gemini (API Google AI Studio, palier gratuit). Les modèles sont essayés dans l'ordre :
# si Google en retire un, le suivant prend le relais. Liste vérifiée le 02/11/2026.
GEMINI_MODELS = ["gemini-3.5-flash-lite", "gemini-3.8-flash", "gemini-2.5-flash"]

HTTP_HEADERS = {"User-Agent": "Mozilla/5.0 (btc-model; +https://github.com)"}
