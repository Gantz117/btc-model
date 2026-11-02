"""Test de causalité de Granger : une variable on-chain X « cause au sens de Granger » le prix
si les valeurs passées de X améliorent significativement la prévision des rendements futurs,
au-delà de ce que permettent les rendements passés seuls. C'est une causalité *prédictive*,
pas une preuve de causalité économique."""
import numpy as np
import pandas as pd
from scipy import stats

from . import config as C


def _ols_rss(y, X):
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    r = y - X @ beta
    return float(r @ r)


def granger_pvalue(y, x, lag):
    """F-test : modèle restreint (lags de y) vs non restreint (lags de y et de x)."""
    df = pd.concat([y.rename("y"), x.rename("x")], axis=1).dropna()
    if len(df) < 120:
        return np.nan, np.nan
    Y = df["y"].values[lag:]
    ylags = np.column_stack([df["y"].values[lag - k:-k] for k in range(1, lag + 1)])
    xlags = np.column_stack([df["x"].values[lag - k:-k] for k in range(1, lag + 1)])
    one = np.ones((len(Y), 1))
    Xr = np.hstack([one, ylags])
    Xu = np.hstack([one, ylags, xlags])
    rss_r, rss_u = _ols_rss(Y, Xr), _ols_rss(Y, Xu)
    dfn, dfd = lag, len(Y) - Xu.shape[1]
    F = ((rss_r - rss_u) / dfn) / (rss_u / dfd)
    p = 1 - stats.f.cdf(F, dfn, dfd)
    # sens de la relation : corrélation entre x(t) et le rendement des 7 jours suivants
    fwd = df["y"].rolling(7).sum().shift(-7)
    corr = df["x"].corr(fwd)
    return float(p), float(corr)


LAGS = (1, 2, 3, 5, 7)


def run_granger(close, chain_X):
    """Pour chaque variable : (1) la variable aide-t-elle à prévoir le prix ? (2) l'inverse :
    le prix aide-t-il à prévoir la variable ? Une variable qui *suit* le prix (2 significatif,
    1 non) décrit le marché mais ne l'anticipe pas.
    On teste plusieurs retards : la meilleure p-value est corrigée (Bonferroni sur les retards),
    sinon on fabriquerait des faux positifs en choisissant le retard après coup."""
    lr = np.log(close).diff()
    end = close.index.max()
    start = end - pd.Timedelta(days=C.GRANGER_WINDOW_DAYS)
    y = lr[lr.index >= start]
    res = []
    for col in chain_X.columns:
        x = chain_X[col][chain_X.index >= start]
        if x.notna().sum() < 200:
            continue
        xs = x.diff() if col in ("mvrv",) else x
        best, rev = (1.0, None, np.nan), 1.0
        for lag in LAGS:
            p, corr = granger_pvalue(y, xs, lag)
            if not np.isnan(p) and p < best[0]:
                best = (p, lag, corr)
            pr, _ = granger_pvalue(xs.diff(), y, lag)
            if not np.isnan(pr):
                rev = min(rev, pr)
        p_adj = min(1.0, best[0] * len(LAGS))
        rev_adj = min(1.0, rev * len(LAGS))
        res.append({"variable": col, "p_value": round(p_adj, 4), "p_brute": round(best[0], 4), "lag": best[1],
                    "corr_7j": None if np.isnan(best[2]) else round(best[2], 3),
                    "significatif": bool(p_adj < C.GRANGER_ALPHA),
                    "p_prix_vers_variable": round(rev_adj, 4),
                    "suit_le_prix": bool(rev_adj < C.GRANGER_ALPHA)})
    res = sorted(res, key=lambda r: r["p_value"])
    # correction de Holm-Bonferroni sur le nombre de variables testées
    m = len(res)
    for i, r in enumerate(res):
        r["p_holm"] = round(min(1.0, r["p_value"] * (m - i)), 4)
    return res


def selected_chain_features(granger_res, min_keep=3):
    """On garde les variables significatives ; à défaut, les `min_keep` meilleures (signalées comme faibles)."""
    sig = [r["variable"] for r in granger_res if r["significatif"]]
    if len(sig) >= min_keep:
        return sig
    return [r["variable"] for r in granger_res[:max(min_keep, len(sig))]]
