"""Point d'entrée. Usage : python -m src.main [--force-backtest] [--offline]"""
import json
import sys
from datetime import datetime, timezone, timedelta

import numpy as np
import pandas as pd

from . import config as C
from . import data_sources as DS
from . import features as F
from . import causality as G
from . import backtest as B
from . import reports as R
from . import regime as RG
from . import levels as LV
from . import pulse as PU
from .model import BTCModel, conf_features, confidence_index, apply_calibration, label


def jdump(obj, path):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o)))


def data_quality():
    w = {"kraken": 0.3, "coinmetrics": 0.25, "fear_greed": 0.15, "news": 0.15}
    q = sum(v for k, v in w.items() if DS.STATUS.get(k, {}).get("ok"))
    bg = [v.get("ok") for k, v in DS.STATUS.items() if k.startswith("bgeo:")]
    q += 0.15 * (np.mean(bg) if bg else 0)
    return round(float(q), 3)


def predict_at(model, X, close, vol, vol_pct, t0, calib, dq):
    """Prévisions faites au lendemain de la clôture t0 : clôture du jour, du dimanche, de fin de mois."""
    p0 = float(close.loc[t0])
    week_end = t0 + pd.Timedelta(days=((6 - t0.day_of_week) % 7) or 7)
    month_end = t0 + pd.offsets.MonthEnd(1)
    targets = {"jour": t0 + pd.Timedelta(days=1), "semaine": week_end, "mois": month_end}
    xrow = X.loc[[t0]]
    preds = {}
    for name, tgt in targets.items():
        h = int((tgt - t0).days)
        q, grp = model.predict_z(xrow, h)
        gz = {k: float(v[0]) for k, v in grp.items()}
        qc = apply_calibration(q[0], h, calib)
        s = float(vol.loc[t0] * np.sqrt(h))
        prices = p0 * np.exp(qc * s)
        cf = conf_features(q[0], gz, float(vol_pct.loc[t0]), h)
        conf = confidence_index(cf, calib, dq)
        preds[name] = {
            "date_cible": str(tgt.date()), "horizon_jours": h, "prix_reference": round(p0, 2),
            "cible": round(float(prices[1]), 0), "bas_80": round(float(prices[0]), 0), "haut_80": round(float(prices[2]), 0),
            "variation_pct": round(100 * (prices[1] / p0 - 1), 2),
            "confiance": conf, "confiance_label": label(conf),
            "direction": "hausse" if prices[1] >= p0 else "baisse",
            "signaux": {g: ("haussier" if v > 0.05 else "baissier" if v < -0.05 else "neutre") for g, v in gz.items()},
            "signaux_z": {g: round(v, 3) for g, v in gz.items()},
            "composantes_confiance": {k: round(v, 3) for k, v in cf.items()},
        }
    return preds


def log_rows(preds, run_date, mode="live"):
    return [{"run_date": run_date, "horizon": k, "target_date": v["date_cible"], "h": v["horizon_jours"],
             "p0": v["prix_reference"], "q10": v["bas_80"], "q50": v["cible"], "q90": v["haut_80"],
             "confidence": v["confiance"], "mode": mode} for k, v in preds.items()]


def bootstrap_log(X, close, vol, vol_pct, groups, calib, months=2):
    """Premier lancement : reconstitue les prévisions des derniers mois complets, avec un modèle
    entraîné uniquement sur les données antérieures (pour que le premier bilan mensuel existe)."""
    last = close.index.max()
    start = (last + pd.Timedelta(days=1)) - pd.offsets.MonthBegin(months)  # 1er jour, N mois en arrière
    model = BTCModel(groups).fit(X, close, vol, cutoff=start)
    rows = []
    for t0 in pd.date_range(start - pd.Timedelta(days=1), last - pd.Timedelta(days=1), freq="D"):
        preds = predict_at(model, X, close, vol, vol_pct, t0, calib, 1.0)
        rows += log_rows(preds, str((t0 + pd.Timedelta(days=1)).date()), "reconstitue")
    print(f"Journal reconstitué : {len(rows)} prévisions depuis {start.date()}")
    return pd.DataFrame(rows)


def main(argv):
    now = datetime.now(timezone.utc)
    today = pd.Timestamp(now.date())
    print(f"=== Modèle BTC — exécution du {now:%Y-%m-%d %H:%M} UTC")
    # Lancements de secours : on ne refait pas le calcul s'il a déjà réussi aujourd'hui.
    latest_f = C.OUT / "latest.json"
    if "--si-pas-fait" in argv and latest_f.exists():
        try:
            if json.loads(latest_f.read_text()).get("genere_le", "")[:10] == str(today.date()):
                print("Calcul déjà fait aujourd'hui : rien à faire.")
                return
        except Exception:
            pass

    # 1) Données
    cm = DS.load_coinmetrics()
    kr = DS.load_kraken()
    close = DS.build_price(cm, kr)
    bgeo = DS.load_bgeometrics()
    fng = DS.load_fear_greed()
    news = DS.load_news()
    print("Sources :", json.dumps(DS.STATUS, ensure_ascii=False, default=str))
    t0 = close.index.max()                     # dernière clôture complète (= veille)
    p0 = float(close.iloc[-1])

    # 2) Variables et causalité
    X, groups = F.build_all(close, cm, bgeo, fng)
    vol = F.ewma_vol(np.log(close).diff())
    vol_pct = vol.rolling(730, min_periods=200).rank(pct=True)
    granger = G.run_granger(close, X[groups["onchain"]])
    groups_live = dict(groups, onchain=G.selected_chain_features(granger))
    groups_live["sentiment"] = [c for c in groups["sentiment"] if X[c].notna().sum() > 365 and c != "news_ia_7d"]
    news_test = None
    if "news_ia_7d" in X and X["news_ia_7d"].notna().sum() >= 200:
        news_test = G.run_granger(close, X[["news_ia_7d"]])
        if news_test and news_test[0]["significatif"] and X["news_ia_7d"].notna().sum() > 365:
            groups_live["sentiment"].append("news_ia_7d")

    # 3) Backtest / calibration (chaque lundi, ou s'il n'existe pas encore)
    bt_f = C.OUT / "backtest.json"
    bt_today = bt_f.exists() and json.loads(bt_f.read_text()).get("genere_le", "")[:10] == str(today.date())
    if "--force-backtest" in argv or not bt_f.exists() or (today.day_of_week == 0 and not bt_today):
        print("Backtest walk-forward en cours (quelques minutes)…")
        bt = B.full(X, close, vol, groups)
        bt["genere_le"] = now.isoformat()
        jdump(bt, bt_f)
    bt = json.loads(bt_f.read_text())
    calib = bt.get("calibration")

    # 4) Entraînement complet et prévisions
    model = BTCModel(groups_live).fit(X, close, vol)
    dq = data_quality()
    preds = predict_at(model, X, close, vol, vol_pct, t0, calib, dq)
    for k, v in preds.items():
        v["cible_affichee"] = bool(C.CIBLE_JOUR) if k == "jour" else True

    regime = RG.current(X, close)
    jdump(regime, C.OUT / "regime.json")
    niveaux = LV.compute(close, cm, bgeo)
    pulse, pulse_nouveau = PU.update()          # BTC Market Pulse de Glassnode (contexte, hors prévision)
    DS.STATUS["glassnode_pulse"] = dict(PU.STATUS)
    latest = {"genere_le": now.isoformat(), "regime": {k: regime[k] for k in ("score", "regime", "depuis", "jours_dans_regime", "lecture")}, "date_derniere_cloture": str(t0.date()), "prix_cloture_veille": round(p0, 2),
              "prix_spot": kr.get("spot"), "volatilite_jour_pct": round(100 * float(vol.loc[t0]), 2),
              "qualite_donnees": dq, "sources": DS.STATUS, "predictions": preds,
              "variables_onchain_retenues": groups_live["onchain"], "granger": granger,
              "variables_sentiment": groups_live["sentiment"], "test_actualite_ia": news_test,
              "jours_historique_actualite_ia": int(X["news_ia_7d"].notna().sum()) if "news_ia_7d" in X else 0,
              "niveaux_cles": niveaux}
    jdump(latest, C.OUT / "latest.json")

    # 5) Journal des prévisions (sert au bilan mensuel)
    log_f = C.OUT / "predictions_log.csv"
    rows = pd.DataFrame(log_rows(preds, str(today.date())))
    if log_f.exists():
        log = pd.read_csv(log_f)
    else:
        log = bootstrap_log(X, close, vol, vol_pct, groups_live, calib)
    log = pd.concat([log[log.run_date != str(today.date())], rows]).sort_values(["run_date", "horizon"])
    log.to_csv(log_f, index=False)

    # 6) Historique pour les graphiques
    hist = close[close.index >= t0 - pd.Timedelta(days=730)]
    jdump({"dates": [str(d.date()) for d in hist.index], "close": [round(float(v), 0) for v in hist.values],
           "sma200": [None if np.isnan(v) else round(float(v), 0) for v in close.rolling(200).mean().reindex(hist.index)],
           "wma200": [None if np.isnan(v) else round(float(v), 0) for v in close.rolling(1400, min_periods=700).mean().reindex(hist.index)],
           "fng": [None if (isinstance(v, float) and np.isnan(v)) else v for v in fng.reindex(hist.index).tolist()] if len(fng) else []},
          C.OUT / "history.json")

    # 7) Synthèse hebdomadaire (le lundi, si elle manque, ou à la parution d'un nouveau Pulse Glassnode)
    iso = today.isocalendar()
    wk_f = C.OUT / "weekly" / f"{iso.year}-S{iso.week:02d}.json"
    if today.day_of_week == 0 or not wk_f.exists() or pulse_nouveau:
        wk = R.weekly(close, X, cm, bgeo, fng, news, granger, latest, log, today, regime, niveaux, pulse)
        jdump(wk, wk_f)
        jdump(wk, C.OUT / "weekly_latest.json")

    # 8) Bilan mensuel (le 1er du mois, ou s'il manque)
    #    (tous les mois terminés présents dans le journal et pas encore évalués)
    ends = sorted({pd.Timestamp(d) + pd.offsets.MonthEnd(0) for d in log["target_date"]})
    for me in [e for e in ends if e <= t0]:
        mo_f = C.OUT / "monthly" / f"{me:%Y-%m}.json"
        if not mo_f.exists() or me == t0:
            mo = R.monthly(close, log, me)
            if mo:
                jdump(mo, mo_f)
                jdump(mo, C.OUT / "monthly_latest.json")
    jdump({"weekly": sorted(p.stem for p in (C.OUT / "weekly").glob("*.json")),
           "monthly": sorted(p.stem for p in (C.OUT / "monthly").glob("*.json"))}, C.OUT / "index.json")
    for k, v in preds.items():
        cible = f"{v['cible']:,.0f} $ " if v["cible_affichee"] else ""
        print(f"{k:8s} → {v['date_cible']} : {cible}fourchette [{v['bas_80']:,.0f} – {v['haut_80']:,.0f}] confiance {v['confiance']} ({v['confiance_label']})")


if __name__ == "__main__":
    main(sys.argv[1:])
