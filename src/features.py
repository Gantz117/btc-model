"""Construction des variables explicatives, regroupées en 3 familles :
 - TECH  : analyse technique et cycles moyen/long terme
 - CHAIN : mouvements on-chain (whales, flux exchanges, cohortes)
 - SENT  : sentiment de marché (Fear & Greed, financement des dérivés)"""
import numpy as np
import pandas as pd

from . import config as C


def ewma_vol(logret, lam=0.94):
    return np.sqrt((logret ** 2).ewm(alpha=1 - lam, adjust=False).mean()).clip(lower=0.004)


def rsi(close, n=14):
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def expanding_powerlaw(close):
    """Régression log(prix) = a + b·log(jours depuis genèse), recalculée chaque jour
    uniquement avec le passé (pas de fuite d'information). Renvoie le résidu et le prix modèle."""
    days = (close.index - pd.Timestamp(C.GENESIS)).days.values.astype(float)
    x, y = np.log(days), np.log(close.values)
    n = np.arange(1, len(x) + 1)
    sx, sy, sxx, sxy = np.cumsum(x), np.cumsum(y), np.cumsum(x * x), np.cumsum(x * y)
    den = n * sxx - sx ** 2
    with np.errstate(divide="ignore", invalid="ignore"):
        b = (n * sxy - sx * sy) / den
        a = (sy - b * sx) / n
    fit = a + b * x
    resid = pd.Series(y - fit, index=close.index)
    resid[:365] = np.nan
    return resid, pd.Series(np.exp(fit), index=close.index), (a[-1], b[-1])


def halving_phase(idx):
    hv = pd.to_datetime(C.HALVINGS)
    last = np.array([hv[hv <= d].max() if (hv <= d).any() else pd.Timestamp("2009-01-03") for d in idx])
    nxt = np.array([hv[hv > d].min() for d in idx])
    since = (idx - pd.DatetimeIndex(last)).days.values
    length = (pd.DatetimeIndex(nxt) - pd.DatetimeIndex(last)).days.values
    return pd.Series(since, idx), pd.Series(since / length, idx)


def tech_features(close):
    lr = np.log(close).diff()
    f = pd.DataFrame(index=close.index)
    f["mayer"] = np.log(close / close.rolling(200).mean())                 # multiple de Mayer (log)
    f["wma200"] = np.log(close / close.rolling(1400, min_periods=700).mean())  # vs moyenne 200 semaines
    f["pl_resid"], _, _ = expanding_powerlaw(close)                          # écart à la loi de puissance
    f["sma50_200"] = np.log(close.rolling(50).mean() / close.rolling(200).mean())
    since, phase = halving_phase(close.index)
    f["halving_phase"] = phase
    f["halving_sin"], f["halving_cos"] = np.sin(2 * np.pi * phase), np.cos(2 * np.pi * phase)
    f["drawdown_ath"] = np.log(close / close.cummax())
    for n in (7, 30, 90, 180):
        f[f"ret_{n}"] = np.log(close / close.shift(n))
    f["rsi14"] = rsi(close)
    f["rsi_w"] = rsi(close.resample("W").last()).reindex(close.index, method="ffill")
    vol = ewma_vol(lr)
    f["vol_ratio"] = np.log(vol / vol.rolling(365, min_periods=90).mean())
    f["dow"] = close.index.day_of_week
    return f


def chain_features(cm, bgeo, idx):
    """Variables on-chain stationnarisées. Décalées d'un jour : la donnée on-chain du jour J
    n'est publiée qu'après la clôture, on n'utilise donc que ce qui était connu au moment de la prévision."""
    cm = cm.reindex(idx).ffill(limit=3)
    f = pd.DataFrame(index=idx)
    sup = cm["SplyCur"]
    net = (cm["FlowInExNtv"] - cm["FlowOutExNtv"]) / sup * 1e4          # flux net vers exchanges (pb de l'offre)
    f["exch_netflow_7d"] = net.rolling(7).sum()
    f["exch_netflow_30d"] = net.rolling(30).sum()
    f["exch_inflow_z"] = ((cm["FlowInExNtv"] - cm["FlowInExNtv"].rolling(90).mean())
                          / cm["FlowInExNtv"].rolling(90).std())          # pic de dépôts (souvent des whales)
    f["exch_reserve_30d"] = np.log(cm["SplyExNtv"] / cm["SplyExNtv"].shift(30))
    f["mvrv"] = np.log(cm["CapMVRVCur"])
    f["mvrv_30d"] = f["mvrv"].diff(30)
    f["active_addr_30d"] = np.log(cm["AdrActCnt"].rolling(7).mean() / cm["AdrActCnt"].rolling(7).mean().shift(30))
    f["hashrate_30d"] = np.log(cm["HashRate"].rolling(7).mean() / cm["HashRate"].rolling(7).mean().shift(30))
    if bgeo is not None and len(bgeo):
        b = bgeo.reindex(idx).ffill(limit=5)
        for col in ("whales_10k", "whales_1k_10k", "sharks_100_1k"):
            if col in b:
                f[f"{col}_7d"] = np.log(b[col] / b[col].shift(7))
                f[f"{col}_30d"] = np.log(b[col] / b[col].shift(30))
        if "sopr" in b:
            f["sopr_7d"] = np.log(b["sopr"]).rolling(7).mean()
        if "etf_flow" in bgeo:
            e = bgeo["etf_flow"].reindex(idx)
            first = bgeo["etf_flow"].first_valid_index()
            e = e.fillna(0).where(idx >= first)          # pas de flux le week-end ; rien avant le lancement des ETF
            f["etf_flow_7d"] = e.rolling(7).sum()
            f["etf_flow_30d"] = e.rolling(30).sum()
            f["etf_flow_z"] = (f["etf_flow_7d"] - f["etf_flow_7d"].rolling(90, min_periods=60).mean()) / f["etf_flow_7d"].rolling(90, min_periods=60).std()
        if "lth_netpos" in b:
            f["lth_netpos_z"] = (b["lth_netpos"] - b["lth_netpos"].rolling(180).mean()) / b["lth_netpos"].rolling(180).std()
    return f.shift(1).replace([np.inf, -np.inf], np.nan)


def load_news_ia(idx):
    f = C.DATA / "news_daily.csv"
    if not f.exists():
        return None
    d = pd.read_csv(f)
    if "score_ia" not in d or d["score_ia"].notna().sum() == 0:
        return None
    s = pd.Series(d["score_ia"].values, index=pd.to_datetime(d["date"])).dropna()
    return s.reindex(idx)


def sent_features(fng, bgeo, idx):
    f = pd.DataFrame(index=idx)
    ia = load_news_ia(idx)
    if ia is not None:
        f["news_ia_7d"] = ia.rolling(7, min_periods=4).mean()
    if len(fng):
        s = fng.reindex(idx).ffill(limit=3)
        f["fng"] = s
        f["fng_7d_chg"] = s.diff(7)
        f["fng_30d_mean"] = s.rolling(30, min_periods=10).mean()
    if bgeo is not None and "funding_rate" in bgeo:
        fr = bgeo["funding_rate"].reindex(idx).ffill(limit=3)
        f["funding_7d"] = fr.rolling(7).mean()
    return f.shift(0)


def build_all(close, cm, bgeo, fng):
    idx = close.index
    T, Ch, S = tech_features(close), chain_features(cm, bgeo, idx), sent_features(fng, bgeo, idx)
    X = pd.concat([T, Ch, S], axis=1)
    groups = {"technique": list(T.columns), "onchain": list(Ch.columns), "sentiment": list(S.columns)}
    return X, groups
