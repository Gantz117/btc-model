"""Modèle de prévision multi-horizon par régression quantile (gradient boosting).
Cible normalisée par la volatilité : z = log(P[t+h]/P[t]) / (σ_t·√h), ce qui rend le problème
comparable entre périodes calmes et agitées. On estime les quantiles 10 %, 50 % et 90 %."""
import numpy as np
import pandas as pd
from scipy.stats import norm
from sklearn.ensemble import HistGradientBoostingRegressor

from . import config as C

HGB = dict(max_iter=150, learning_rate=0.03, max_leaf_nodes=12, min_samples_leaf=600,
           l2_regularization=1.0, random_state=42)


def make_dataset(X, close, vol, feats, horizons, cutoff=None):
    """Empile (date, horizon). `cutoff` : seules les cibles connues avant cette date sont gardées."""
    lp = np.log(close)
    parts = []
    for h in horizons:
        y = (lp.shift(-h) - lp) / (vol * np.sqrt(h))
        d = X[feats].copy()
        d["h"] = h
        d["y"] = y
        d["t"] = d.index
        target_date = d.index + pd.Timedelta(days=h)
        mask = d["y"].notna() & (d.index >= C.TRAIN_START)
        if cutoff is not None:
            mask &= target_date < cutoff
        parts.append(d[mask])
    return pd.concat(parts, ignore_index=True)


class BTCModel:
    def __init__(self, groups):
        self.groups = groups  # {"technique": [...], "onchain": [...], "sentiment": [...]}
        self.all_feats = [f for g in groups.values() for f in g]
        self.models = {}

    def fit(self, X, close, vol, cutoff=None, horizons=C.TRAIN_HORIZONS):
        D = make_dataset(X, close, vol, self.all_feats, horizons, cutoff)
        cols = self.all_feats + ["h"]
        for q in C.QUANTILES:
            m = HistGradientBoostingRegressor(loss="quantile", quantile=q, **HGB)
            m.fit(D[cols], D["y"])
            self.models[("all", q)] = m
        for g, feats in self.groups.items():
            if not feats:
                continue
            m = HistGradientBoostingRegressor(loss="quantile", quantile=0.5, **HGB)
            m.fit(D[feats + ["h"]], D["y"])
            self.models[(g, 0.5)] = m
        self.n_train = len(D)
        return self

    def predict_z(self, xrow, h):
        """xrow : DataFrame d'une ou plusieurs lignes (dates) ; h : horizon scalaire ou array."""
        x = xrow[self.all_feats].copy()
        x["h"] = h
        qs = np.column_stack([self.models[("all", q)].predict(x[self.all_feats + ["h"]]) for q in C.QUANTILES])
        qs = np.sort(qs, axis=1)  # évite le croisement des quantiles
        grp = {}
        for g, feats in self.groups.items():
            if (g, 0.5) in self.models:
                grp[g] = self.models[(g, 0.5)].predict(x[feats + ["h"]])
        return qs, grp


def conf_features(q, grp_z, vol_pct, h):
    """Éléments bruts qui servent à l'indice de confiance."""
    q10, q50, q90 = q
    s = max((q90 - q10) / (2 * 1.2816), 1e-6)
    p_up = float(norm.cdf(q50 / s))
    sign = 1.0 if q50 >= 0 else -1.0
    vals = list(grp_z.values())
    agreement = float(np.mean([np.sign(v) == sign for v in vals])) if vals else 0.5
    return {"force_signal": abs(p_up - 0.5) * 2, "accord_sous_modeles": agreement,
            "calme_marche": 1 - float(np.clip(vol_pct if vol_pct == vol_pct else 0.5, 0, 1)),
            "log_h": float(np.log(h)), "proba_hausse_brute": p_up}


CONF_KEYS = ["force_signal", "accord_sous_modeles", "calme_marche", "log_h"]


def confidence_index(cf, calib, data_quality=1.0):
    """Indice de confiance (0–100) = probabilité, calibrée sur l'historique (backtest), que le prix
    termine du côté annoncé (hausse ou baisse par rapport au prix de référence).
    50 = pile ou face ; au-delà, le modèle a historiquement eu raison plus souvent qu'il n'a eu tort
    dans des situations comparables. Les données manquantes ramènent l'indice vers 50."""
    if calib and calib.get("conf_coef") is not None:
        z = calib["conf_intercept"] + sum(c * cf[k] for c, k in zip(calib["conf_coef"], CONF_KEYS))
        p = 1 / (1 + np.exp(-z))
    else:
        p = 0.5 + 0.1 * cf["force_signal"] * cf["accord_sous_modeles"]
    p = 0.5 + (p - 0.5) * data_quality
    return round(100 * float(p), 1)


def label(conf):
    return "élevée" if conf >= 58 else ("moyenne" if conf >= 53 else "faible")


def apply_calibration(q, h, calib):
    """Rétrécit la médiane vers la marche aléatoire (facteur k) et ajuste la largeur de l'intervalle
    (facteur c) pour que, historiquement, 80 % des prix réalisés tombent dedans."""
    key = "1" if h <= 2 else ("7" if h <= 12 else "30")
    k, c = 1.0, 1.0
    if calib and key in calib.get("horizons", {}):
        k, c = calib["horizons"][key]["k"], calib["horizons"][key]["c"]
    q10, q50, q90 = q
    m = k * q50
    return np.array([m - c * (q50 - q10), m, m + c * (q90 - q50)])
