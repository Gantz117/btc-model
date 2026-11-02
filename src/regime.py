"""Indicateur de régime de marché : où en est le Bitcoin dans son cycle ?

Idée : les modèles ne savent pas dire où sera le prix dans une semaine, mais plusieurs
indicateurs de valorisation long terme (écart à la loi de puissance, multiple de Mayer, MVRV,
moyenne 200 semaines, drawdown) ont historiquement séparé les zones d'accumulation des zones
de surchauffe. On les combine en un score de -1 (accumulation) à +1 (surchauffe).

Sans fuite d'information : chaque indicateur est converti en percentile *de son propre passé*
(fenêtre glissante de 4 ans), pas de l'historique complet. Le régime du jour J n'utilise que
ce qui était connu le jour J."""
import numpy as np
import pandas as pd

WINDOW = 4 * 365  # un cycle de halving environ

COMPONENTS = {  # nom : (colonne, sens : +1 si une valeur haute = surchauffe)
    "loi_puissance": ("pl_resid", +1),
    "mayer": ("mayer", +1),
    "mvrv": ("mvrv", +1),
    "moyenne_200_semaines": ("wma200", +1),
    "drawdown_ath": ("drawdown_ath", +1),   # proche de 0 = proche de l'ATH = chaud ; très négatif = froid
    "rsi_hebdo": ("rsi_w", +1),
}
LABELS = {
    "accumulation profonde": (-np.inf, -0.6),
    "accumulation": (-0.6, -0.25),
    "neutre": (-0.25, 0.25),
    "échauffement": (0.25, 0.6),
    "surchauffe": (0.6, np.inf),
}


def rolling_pct(s, window=WINDOW, min_periods=365):
    """Percentile (0–1) de la dernière valeur dans la fenêtre glissante passée."""
    return s.rolling(window, min_periods=min_periods).rank(pct=True)


def score(X):
    parts = {}
    for name, (col, sign) in COMPONENTS.items():
        if col in X:
            parts[name] = sign * (2 * rolling_pct(X[col]) - 1)
    P = pd.DataFrame(parts)
    s = P.mean(axis=1, skipna=True)
    s[P.notna().sum(axis=1) < 3] = np.nan
    s = s.rolling(14, min_periods=7).mean()   # lissage : un régime se juge sur des semaines, pas sur un jour
    return s, P


def label(v):
    if v is None or np.isnan(v):
        return None
    for k, (lo, hi) in LABELS.items():
        if lo <= v < hi:
            return k
    return None


def validate(close, s, horizons=(90, 180, 365)):
    """Pour chaque régime : distribution des rendements futurs (médiane, % positif, pire cas).
    Les observations journalières se recouvrent (un régime dure des mois) : le nombre de
    *périodes* distinctes est indiqué pour rappeler que l'échantillon effectif est petit."""
    lab = s.map(label)
    out = {}
    for h in horizons:
        fwd = np.log(close.shift(-h) / close)
        d = pd.DataFrame({"lab": lab, "fwd": fwd}).dropna()
        res = {}
        for k in LABELS:
            g = d[d.lab == k]["fwd"]
            if len(g) < 30:
                continue
            episodes = int(((lab == k) & (lab.shift(1) != k)).sum())
            res[k] = {"n_jours": int(len(g)), "n_episodes": episodes,
                      "rendement_median_pct": round(100 * (np.exp(g.median()) - 1), 1),
                      "pct_positif": round(100 * float((g > 0).mean()), 1),
                      "pire_cas_pct": round(100 * (np.exp(g.quantile(0.05)) - 1), 1),
                      "meilleur_cas_pct": round(100 * (np.exp(g.quantile(0.95)) - 1), 1)}
        out[str(h)] = res
    # référence : toutes périodes confondues
    for h in horizons:
        fwd = np.log(close.shift(-h) / close).dropna()
        fwd = fwd[fwd.index >= s.dropna().index.min()]
        out[str(h)]["toutes_periodes"] = {"n_jours": int(len(fwd)), "rendement_median_pct": round(100 * (np.exp(fwd.median()) - 1), 1),
                                          "pct_positif": round(100 * float((fwd > 0).mean()), 1),
                                          "pire_cas_pct": round(100 * (np.exp(fwd.quantile(0.05)) - 1), 1),
                                          "meilleur_cas_pct": round(100 * (np.exp(fwd.quantile(0.95)) - 1), 1)}
    return out


def current(X, close):
    s, P = score(X)
    v = float(s.iloc[-1])
    lab = s.map(label)
    # depuis quand dans ce régime ?
    cur = lab.iloc[-1]
    since = lab.index[-1]
    for t in reversed(lab.index[:-1]):
        if lab.loc[t] != cur:
            break
        since = t
    hist = s[s.index >= s.index.max() - pd.Timedelta(days=730)]
    return {
        "score": round(v, 3), "regime": cur, "depuis": str(since.date()),
        "jours_dans_regime": int((lab.index[-1] - since).days) + 1,
        "composantes": {k: round(float(P[k].iloc[-1]), 3) for k in P.columns if not np.isnan(P[k].iloc[-1])},
        "score_il_y_a_30j": round(float(s.iloc[-31]), 3),
        "historique": {"dates": [str(d.date()) for d in hist.index[::3]], "score": [round(float(x), 3) for x in hist.values[::3]]},
        "validation": validate(close, s),
        "seuils": {k: [None if np.isinf(lo) else lo, None if np.isinf(hi) else hi] for k, (lo, hi) in LABELS.items()},
        "lecture": _reading(cur, v),
    }


def _reading(cur, v):
    base = {
        "accumulation profonde": "Les indicateurs de valorisation long terme sont tous dans le bas de leur historique récent. "
                                 "Par le passé, ces zones ont précédé les meilleurs rendements à 6–12 mois, mais elles peuvent durer des mois et le prix peut encore baisser.",
        "accumulation": "Valorisation basse par rapport aux 4 dernières années. Historiquement favorable à un horizon de 6–12 mois, sans garantie sur le court terme.",
        "neutre": "Valorisation dans la moyenne : les indicateurs de cycle ne donnent pas de signal directionnel net. C'est le régime le plus fréquent.",
        "échauffement": "Valorisation élevée. Historiquement, les rendements à 6–12 mois ont été plus dispersés et les corrections plus fréquentes qu'en zone neutre.",
        "surchauffe": "Tous les indicateurs sont dans le haut de leur historique. Les sommets de cycle précédents ont été atteints dans ce régime ; le risque de forte correction est maximal.",
    }
    return f"Score {v:+.2f} : {cur}. {base.get(cur, '')} Rappel : trois cycles observés seulement, et les régimes extrêmes ne reviennent que quelques fois par décennie."
