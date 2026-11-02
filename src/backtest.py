"""Backtest walk-forward : on ré-entraîne le modèle périodiquement en n'utilisant que le passé,
puis on compare ses prévisions à la « marche aléatoire » (prévision naïve : le prix ne bouge pas).
Le backtest sert aussi à calibrer le modèle (rétrécissement de la médiane, largeur des
intervalles, indice de confiance). Pour rester honnête, la calibration est apprise sur la
première partie de l'historique et évaluée sur la seconde."""
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

from . import causality as G
from .model import BTCModel, conf_features, confidence_index, apply_calibration, CONF_KEYS

EVAL_H = {"jour": 1, "semaine": 7, "mois": 30}
HKEY = {1: "1", 7: "7", 30: "30"}


def run(X, close, vol, groups_base, start="2020-01-01", step_days=91):
    vol_pct = vol.rolling(730, min_periods=200).rank(pct=True)
    recs = []
    refits = pd.date_range(start, close.index.max() - pd.Timedelta(days=2), freq=f"{step_days}D")
    for R in refits:
        hist = close[close.index < R]
        gr = G.run_granger(hist, X.loc[X.index < R, groups_base["onchain"]])
        groups = dict(groups_base, onchain=G.selected_chain_features(gr))
        groups["sentiment"] = [c for c in groups["sentiment"] if X.loc[X.index < R, c].notna().sum() > 365]
        m = BTCModel(groups).fit(X, close, vol, cutoff=R)
        test = X[(X.index >= R) & (X.index < R + pd.Timedelta(days=step_days))]
        for name, h in EVAL_H.items():
            q, grp = m.predict_z(test, h)
            for i, t in enumerate(test.index):
                tgt = t + pd.Timedelta(days=h)
                if tgt not in close.index:
                    continue
                cf = conf_features(q[i], {k: v[i] for k, v in grp.items()}, vol_pct.loc[t], h)
                recs.append({"t": t, "horizon": name, "h": h, "p0": close.loc[t], "s": vol.loc[t] * np.sqrt(h),
                             "z10": q[i, 0], "z50": q[i, 1], "z90": q[i, 2], "actual": close.loc[tgt], **cf})
    return pd.DataFrame(recs)


def calibrate(df):
    """Ajuste k (rétrécissement), c (largeur d'intervalle) et la régression logistique de confiance."""
    cal = {"horizons": {}}
    for h, g in df.groupby("h"):
        za = np.log(g["actual"] / g["p0"]) / g["s"]
        ks = np.linspace(0, 1, 21)
        errs = [np.mean(np.abs(za - k * g["z50"])) for k in ks]
        k = float(ks[int(np.argmin(errs))])
        cs = np.linspace(0.6, 3.0, 49)
        cov = []
        for c in cs:
            lo = k * g["z50"] - c * (g["z50"] - g["z10"])
            hi = k * g["z50"] + c * (g["z90"] - g["z50"])
            cov.append(np.mean((za >= lo) & (za <= hi)))
        c = float(cs[int(np.argmin(np.abs(np.array(cov) - 0.80)))])
        cal["horizons"][HKEY.get(int(h), str(h))] = {"k": round(k, 3), "c": round(c, 3)}
    mv = np.sign(df["actual"] - df["p0"])
    ok = (mv == np.sign(df["z50"]).replace(0, 1)) & (mv != 0)
    lr = LogisticRegression(C=0.1).fit(df[CONF_KEYS], ok.astype(int))
    cal["conf_coef"] = [round(float(x), 5) for x in lr.coef_[0]]
    cal["conf_intercept"] = round(float(lr.intercept_[0]), 5)
    return cal


def to_prices(df, calib):
    """Convertit les z bruts en prix (q10/q50/q90) et calcule l'indice de confiance."""
    out = df.copy()
    rows = []
    for r in df.itertuples():
        q = apply_calibration((r.z10, r.z50, r.z90), r.h, calib)
        cf = {k: getattr(r, k) for k in CONF_KEYS}
        rows.append((*(r.p0 * np.exp(q * r.s)), confidence_index(cf, calib)))
    out[["q10", "q50", "q90", "confidence"]] = rows
    return out


def evaluate(df):
    """Statistiques de précision par horizon (backtest ET bilan mensuel).
    Attend les colonnes : horizon, p0, q10, q50, q90, actual, confidence."""
    out = {}
    for name, g in df.groupby("horizon"):
        err = (g["q50"] - g["actual"]).abs() / g["actual"]
        naive = (g["p0"] - g["actual"]).abs() / g["actual"]
        move = np.sign(g["actual"] - g["p0"])
        pred = np.sign(g["q50"] - g["p0"])
        dir_ok = (move == pred)[move != 0]
        cover = ((g["actual"] >= g["q10"]) & (g["actual"] <= g["q90"])).mean()
        res = {"n": int(len(g)), "mape_modele": round(100 * err.mean(), 2), "mape_naif": round(100 * naive.mean(), 2),
               "gain_vs_naif_pct": round(100 * (1 - err.mean() / naive.mean()), 1) if naive.mean() > 0 else None,
               "direction_ok_pct": round(100 * dir_ok.mean(), 1) if len(dir_ok) else None,
               "couverture_80_pct": round(100 * cover, 1),
               "taux_hausse_pct": round(100 * float((move > 0).mean()), 1),  # référence « toujours haussier »
               "confiance_moyenne": round(float(g["confidence"].mean()), 1)}
        b = pd.cut(g["confidence"], [0, 53, 58, 101], labels=["faible", "moyenne", "élevée"], right=False)
        cal = {}
        for lab, gg in g.groupby(b, observed=True):
            mv, pr = np.sign(gg["actual"] - gg["p0"]), np.sign(gg["q50"] - gg["p0"])
            okk = (mv == pr)[mv != 0]
            cal[str(lab)] = {"n": int(len(gg)), "confiance_moy": round(float(gg["confidence"].mean()), 1),
                             "direction_ok_pct": round(100 * okk.mean(), 1) if len(okk) else None}
        res["par_confiance"] = cal
        out[name] = res
    return out


def full(X, close, vol, groups, split="2024-01-01"):
    raw = run(X, close, vol, groups)
    first, second = raw[raw.t < split], raw[raw.t >= split]
    cal_first = calibrate(first)
    oos = to_prices(second, cal_first)          # évaluation honnête (calibration jamais vue)
    calib_live = calibrate(raw)                  # calibration utilisée en production
    raw_eval = to_prices(second, {"horizons": {k: {"k": 1, "c": 1} for k in ("1", "7", "30")}})
    return {
        "periode_evaluation": f"{split} → {str(close.index.max().date())}",
        "periode_calibration": f"{str(raw.t.min().date())} → {split}",
        "resultats": evaluate(oos),
        "resultats_sans_calibration": evaluate(raw_eval),
        "calibration": calib_live,
        "serie": oos[oos.horizon == "mois"][["t", "p0", "q10", "q50", "q90", "actual"]]
                 .assign(t=lambda d: d.t.dt.strftime("%Y-%m-%d")).round(0).to_dict("records")[::3],
    }
