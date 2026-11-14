"""Niveaux clés de prix de revient (lecture « à la Glassnode »).

- Prix réalisé : prix moyen auquel chaque bitcoin a bougé pour la dernière fois (CoinMetrics : prix / MVRV), quotidien.
- Prix de revient des acheteurs récents (< 155 jours, « STH cost basis ») : BGeometrics, palier gratuit
  publié avec 7 jours de retard.
- True market mean : prix de revient moyen des coins « actifs » (hors coins dormants) : BGeometrics, 7 jours de retard.
  Attention : sa méthode diffère selon les fournisseurs (le 07/11/2026, 79,1 k$ chez BGeometrics contre 77,5 k$
  cité par Glassnode le 09/11).

Ces niveaux servent à LIRE le marché (au-dessus = les acheteurs concernés sont en profit en moyenne).
Ils n'entrent pas dans la prévision tant qu'un test ne l'a pas justifié."""
import numpy as np
import pandas as pd

LEVELS = [
    ("sth_cost_basis", "Prix de revient des acheteurs récents (< 155 j)", "BGeometrics"),
    ("true_market_mean", "True market mean (prix de revient des coins actifs)", "BGeometrics"),
    ("realized_price", "Prix réalisé (prix de revient moyen de tout le marché)", "CoinMetrics"),
]


def compute(close, cm, bgeo):
    p = float(close.iloc[-1])
    t0 = close.index.max()
    series = {}
    if "CapMVRVCur" in cm and "PriceUSD" in cm:
        series["realized_price"] = (cm["PriceUSD"] / cm["CapMVRVCur"]).dropna()
    for k in ("sth_cost_basis", "true_market_mean"):
        if bgeo is not None and k in bgeo:
            series[k] = bgeo[k].dropna()
    out = []
    for key, label, src in LEVELS:
        s = series.get(key)
        if s is None or s.empty:
            continue
        v, d = float(s.iloc[-1]), s.index[-1]
        # position du prix une semaine plus tôt, pour repérer un franchissement récent
        prev_p = float(close.loc[:t0 - pd.Timedelta(days=7)].iloc[-1])
        prev_v = float(s.loc[:t0 - pd.Timedelta(days=7)].iloc[-1]) if (s.index <= t0 - pd.Timedelta(days=7)).any() else v
        now_above, before_above = p >= v, prev_p >= prev_v
        out.append({"cle": key, "nom": label, "valeur": round(v, 0), "date": str(d.date()), "source": src,
                    "retard_jours": int((t0 - d).days), "ecart_pct": round(100 * (p / v - 1), 1),
                    "position": "au-dessus" if now_above else "en dessous",
                    "franchi_7j": None if now_above == before_above else ("à la hausse" if now_above else "à la baisse")})
    return out


def _usd(v):
    return f"{v:,.0f}".replace(",", "\u202f") + " $"


def attention_points(levels):
    pts = []
    for l in levels:
        if l["franchi_7j"]:
            pts.append(f"Le prix a franchi {l['franchi_7j']} le niveau « {l['nom']} » ({_usd(l['valeur'])}) cette semaine.")
        elif l["cle"] == "sth_cost_basis":
            pts.append(f"Prix {l['position']} du prix de revient des acheteurs récents ({_usd(l['valeur'])}, écart {l['ecart_pct']:+.1f} %) : "
                       + ("ils sont en profit en moyenne." if l["position"] == "au-dessus"
                          else "ils sont en perte en moyenne, d'où un risque de ventes à l'équilibre lors d'un rebond."))
    return pts
