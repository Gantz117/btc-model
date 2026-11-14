"""Synthèse hebdomadaire des informations utilisées et bilan mensuel des prévisions."""
import numpy as np
import pandas as pd

from . import config as C
from .backtest import evaluate
from .features import expanding_powerlaw, halving_phase
from . import llm
from .levels import attention_points as level_points

LABELS = {
    "exch_netflow_7d": "Flux net vers les exchanges (7 j)",
    "exch_netflow_30d": "Flux net vers les exchanges (30 j)",
    "exch_inflow_z": "Pic de dépôts sur les exchanges (z-score)",
    "exch_reserve_30d": "Réserves des exchanges (variation 30 j)",
    "mvrv": "MVRV (valeur de marché / valeur réalisée)",
    "mvrv_30d": "MVRV (variation 30 j)",
    "active_addr_30d": "Adresses actives (variation 30 j)",
    "hashrate_30d": "Hashrate (variation 30 j)",
    "whales_10k_7d": "BTC des whales > 10 000 BTC (7 j)",
    "whales_10k_30d": "BTC des whales > 10 000 BTC (30 j)",
    "whales_1k_10k_7d": "BTC des whales 1 000–10 000 BTC (7 j)",
    "whales_1k_10k_30d": "BTC des whales 1 000–10 000 BTC (30 j)",
    "sharks_100_1k_7d": "BTC des adresses 100–1 000 BTC (7 j)",
    "sharks_100_1k_30d": "BTC des adresses 100–1 000 BTC (30 j)",
    "sopr_7d": "SOPR moyen 7 j (prises de profit / pertes)",
    "lth_netpos_z": "Position nette des détenteurs long terme (z-score)",
    "etf_flow_7d": "Flux nets des ETF spot (7 j, BTC)",
    "etf_flow_30d": "Flux nets des ETF spot (30 j, BTC)",
    "etf_flow_z": "Flux ETF 7 j inhabituels (z-score)",
}
READING = {  # sens de lecture « classique » d'une hausse de la variable
    "exch_netflow_7d": "plus de BTC envoyés vers les exchanges : pression vendeuse potentielle",
    "exch_netflow_30d": "plus de BTC envoyés vers les exchanges : pression vendeuse potentielle",
    "exch_inflow_z": "dépôts inhabituels sur les exchanges (souvent des gros portefeuilles)",
    "exch_reserve_30d": "réserves des exchanges en hausse : offre disponible à la vente en hausse",
    "whales_10k_7d": "les très gros portefeuilles accumulent",
    "whales_10k_30d": "les très gros portefeuilles accumulent",
    "whales_1k_10k_7d": "les whales accumulent",
    "whales_1k_10k_30d": "les whales accumulent",
    "sharks_100_1k_7d": "les gros portefeuilles accumulent",
    "sharks_100_1k_30d": "les gros portefeuilles accumulent",
    "lth_netpos_z": "les détenteurs long terme accumulent",
    "mvrv": "le marché est plus loin de son prix de revient moyen (plus « cher »)",
    "sopr_7d": "les coins dépensés le sont avec plus de profit (prises de bénéfices)",
    "etf_flow_7d": "les ETF achètent : demande institutionnelle",
    "etf_flow_30d": "les ETF achètent : demande institutionnelle",
    "etf_flow_z": "achats ETF inhabituellement forts",
}


def _f(x, nd=2):
    return None if x is None or (isinstance(x, float) and np.isnan(x)) else round(float(x), nd)


def cycle_summary(close, X):
    t = close.index.max()
    p = close.iloc[-1]
    resid, pl_price, _ = expanding_powerlaw(close)
    since, phase = halving_phase(close.index)
    sma200, wma200 = close.rolling(200).mean().iloc[-1], close.rolling(1400).mean().iloc[-1]
    ath = close.max()
    r = resid.dropna()
    pct = float((r < r.iloc[-1]).mean())
    s50, s200 = close.rolling(50).mean().iloc[-1], sma200
    d = {
        "jours_depuis_halving": int(since.iloc[-1]), "phase_cycle_pct": round(100 * phase.iloc[-1], 1),
        "multiple_mayer": _f(p / sma200), "ecart_200_semaines_pct": _f(100 * (p / wma200 - 1), 1),
        "prix_loi_puissance": _f(pl_price.iloc[-1], 0), "ecart_loi_puissance_pct": _f(100 * (np.exp(r.iloc[-1]) - 1), 1),
        "percentile_loi_puissance": round(100 * pct, 0), "ath": _f(ath, 0), "ecart_ath_pct": _f(100 * (p / ath - 1), 1),
        "rsi_hebdo": _f(X["rsi_w"].iloc[-1], 1), "rsi_jour": _f(X["rsi14"].iloc[-1], 1),
        "tendance_50_200": "croisement haussier (MM50 > MM200)" if s50 > s200 else "croisement baissier (MM50 < MM200)",
        "perf_7j_pct": _f(100 * (p / close.iloc[-8] - 1), 1), "perf_30j_pct": _f(100 * (p / close.iloc[-31] - 1), 1),
    }
    ph = d["phase_cycle_pct"]
    hist = ("Historiquement, les sommets de cycle sont intervenus 12 à 18 mois après le halving "
            "et les creux environ 12 mois après le sommet (seulement 3 cycles complets observés : prudence).")
    if ph < 25:
        txt = "début de cycle post-halving"
    elif ph < 45:
        txt = "milieu de cycle, zone où les précédents sommets ont été atteints"
    elif ph < 75:
        txt = "seconde moitié de cycle, historiquement phase de correction/marché baissier"
    else:
        txt = "fin de cycle, historiquement phase d'accumulation avant le halving suivant"
    d["lecture"] = (f"{d['jours_depuis_halving']} jours depuis le dernier halving ({ph} % du cycle) : {txt}. {hist} "
                    f"Le prix est à {d['ecart_loi_puissance_pct']:+} % de la loi de puissance "
                    f"(percentile {int(d['percentile_loi_puissance'])} de l'historique), multiple de Mayer {d['multiple_mayer']}, "
                    f"{d['ecart_200_semaines_pct']:+} % au-dessus de la moyenne 200 semaines. Tendance : {d['tendance_50_200']}.")
    return d


def onchain_summary(X, granger):
    out = []
    for g in granger:
        v = g["variable"]
        s = X[v].dropna()
        if s.empty:
            continue
        last = s.iloc[-1]
        yr = s[s.index >= s.index.max() - pd.Timedelta(days=365)]
        z = (last - yr.mean()) / yr.std() if yr.std() > 0 else 0
        trend = "en hausse" if z > 0.5 else ("en baisse" if z < -0.5 else "dans la moyenne")
        reading = READING.get(v, "")
        out.append({"variable": v, "libelle": LABELS.get(v, v), "valeur": _f(last, 4), "z_1an": _f(z, 2),
                    "tendance": trend, "lecture_si_hausse": reading, "p_value": g["p_value"], "p_holm": g["p_holm"],
                    "retard_jours": g["lag"], "corr_rendement_7j": g["corr_7j"], "significatif": g["significatif"],
                    "p_prix_vers_variable": g.get("p_prix_vers_variable"), "suit_le_prix": g.get("suit_le_prix")})
    n_sig = sum(o["significatif"] for o in out)
    n_holm = sum(1 for o in out if o["p_holm"] < C.GRANGER_ALPHA)
    n_follow = sum(1 for o in out if o.get("suit_le_prix"))
    msg = (f"Sur 3 ans, {n_sig} variable(s) on-chain/ETF sur {len(out)} aident significativement à prévoir le prix "
           f"(p < 5 % après correction pour les retards testés) ; {n_holm} après correction pour le nombre de variables (Holm). "
           f"À l'inverse, le prix aide à prévoir {n_follow} d'entre elles : ces variables suivent le marché plus qu'elles ne l'anticipent. "
           f"Une causalité de Granger est prédictive, pas une preuve de lien de cause à effet.")
    return {"variables": out, "lecture": msg}


def sentiment_summary(fng, news, today):
    d = {}
    if len(fng):
        f = fng.dropna()
        d["fear_greed"] = {"actuel": _f(f.iloc[-1], 0), "il_y_a_7j": _f(f.iloc[-8], 0) if len(f) > 8 else None,
                           "moyenne_30j": _f(f.iloc[-30:].mean(), 0)}
        v = f.iloc[-1]
        d["fear_greed"]["zone"] = ("peur extrême" if v < 25 else "peur" if v < 45 else "neutre" if v <= 55
                                   else "avidité" if v <= 75 else "avidité extrême")
    start = (today - pd.Timedelta(days=7)).strftime("%Y-%m-%d")
    wk = [n for n in news if n["date"] >= start]
    d["actualites"] = {"n": len(wk), "score_moyen": _f(np.mean([n["score"] for n in wk]), 3) if wk else None}
    tags = {}
    for n in wk:
        for t in n["tags"]:
            tags[t] = tags.get(t, 0) + 1
    d["themes"] = dict(sorted(tags.items(), key=lambda x: -x[1]))
    srt = sorted(wk, key=lambda n: n["score"])
    keep = ["title", "link", "source", "date", "score", "tags"]
    d["titres_negatifs"] = [{k: n[k] for k in keep} for n in srt[:5] if n["score"] < 0]
    d["titres_positifs"] = [{k: n[k] for k in keep} for n in srt[::-1][:5] if n["score"] > 0]
    # titres « importants » : ceux qui touchent ETF/Régulation/Macro/Whales, les plus marqués d'abord
    if any("llm_importance" in n for n in wk):   # Gemini a jugé l'importance de chaque titre
        imp = sorted(wk, key=lambda n: -n.get("llm_importance", 0))
    else:
        imp = sorted([n for n in wk if set(n["tags"]) & {"ETF", "Régulation", "Macro / Fed", "Whales", "Institutionnels"}],
                     key=lambda n: -abs(n["score"]))
    d["titres_importants"] = [{k: n.get(k) for k in keep + ["llm_importance"]} for n in imp[:8]]
    s = d["actualites"]["score_moyen"]
    tone = "neutre" if s is None or abs(s) < 0.05 else ("plutôt positif" if s > 0 else "plutôt négatif")
    fg = d.get("fear_greed", {})
    fg_txt = (f"Fear & Greed à {fg.get('actuel'):.0f} ({fg.get('zone')})"
              + (f", contre {fg.get('il_y_a_7j'):.0f} il y a 7 jours. " if fg.get('il_y_a_7j') is not None else ". ")
              if fg else "Indice Fear & Greed indisponible cette semaine. ")
    d["lecture"] = (fg_txt + f"Ton des {len(wk)} titres de la semaine ({'notés par Gemini' if any('llm_impact' in n for n in wk) else 'notés par lexique'}) : {tone}. Thèmes dominants : "
                    f"{', '.join(list(d['themes'])[:3]) or 'aucun'}. Le score d'actualité n'entre pas encore dans le calcul de la cible "
                    f"(pas d'historique suffisant pour le valider) ; il est fourni à titre qualitatif.")
    return d


def past_week_perf(close, log, today):
    if log is None or log.empty:
        return None
    start, end = today - pd.Timedelta(days=7), today - pd.Timedelta(days=1)
    l = log[log.horizon == "jour"].copy()
    l["target_date"] = pd.to_datetime(l["target_date"])
    l = l[(l.target_date >= start) & (l.target_date <= end)]
    l["actual"] = l.target_date.map(close)
    l = l.dropna(subset=["actual"])
    if l.empty:
        return None
    return {"n": int(len(l)), "erreur_moyenne_pct": _f(100 * ((l.q50 - l.actual).abs() / l.actual).mean(), 2),
            "dans_intervalle_pct": _f(100 * ((l.actual >= l.q10) & (l.actual <= l.q90)).mean(), 0),
            "detail": [{"date": str(r.target_date.date()), "cible": r.q50, "reel": round(r.actual, 0)} for r in l.itertuples()]}


def weekly(close, X, cm, bgeo, fng, news, granger, latest, log, today, regime=None, niveaux=None, pulse=None):
    cyc = cycle_summary(close, X)
    oc = onchain_summary(X, granger)
    se = sentiment_summary(fng, news, today)
    p = latest["predictions"]
    pts = []
    if regime:
        chg = regime["score"] - regime["score_il_y_a_30j"]
        pts.append(f"Régime de marché : {regime['regime']} (score {regime['score']:+.2f}, {'en hausse' if chg > 0.1 else 'en baisse' if chg < -0.1 else 'stable'} sur 30 j), depuis le {regime['depuis']}.")
    if cyc["percentile_loi_puissance"] >= 85:
        pts.append("Prix historiquement élevé par rapport à la loi de puissance : risque de correction accru.")
    if cyc["percentile_loi_puissance"] <= 15:
        pts.append("Prix historiquement bas par rapport à la loi de puissance : zone d'accumulation par le passé.")
    if (cyc["multiple_mayer"] or 1) > 2.4:
        pts.append("Multiple de Mayer > 2,4 : niveau historiquement associé aux excès haussiers.")
    if (cyc["multiple_mayer"] or 1) < 0.8:
        pts.append("Multiple de Mayer < 0,8 : niveau historiquement associé aux creux de marché.")
    fg = se.get("fear_greed", {}).get("actuel")
    if fg is not None and fg <= 20:
        pts.append("Peur extrême : historiquement souvent proche de points bas locaux (signal contrarien).")
    if fg is not None and fg >= 80:
        pts.append("Avidité extrême : historiquement souvent proche de sommets locaux (signal contrarien).")
    for v in oc["variables"]:
        if v["significatif"] and abs(v["z_1an"] or 0) > 1.5:
            pts.append(f"{v['libelle']} anormalement {('élevé' if v['z_1an'] > 0 else 'bas')} (z = {v['z_1an']}).")
    pts += level_points(niveaux or [])
    if pulse and pulse.get("extremes"):
        ext = [f"{e['nom_fr']} ({e['zone']})" for e in pulse["extremes"][:4]]
        pts.append(f"Glassnode Market Pulse ({pulse['publie_le']}) : indicateurs en zone extrême : " + " ; ".join(ext) + ".")
    sig = p["semaine"]["signaux"]
    pts.append("Signaux des sous-modèles (semaine) : " + ", ".join(f"{k} {v}" for k, v in sig.items()) + ".")
    start = (today - pd.Timedelta(days=7)).strftime("%Y-%m-%d")
    facts = {"prix": latest["prix_cloture_veille"], "regime": {k: regime[k] for k in ("score", "regime")} if regime else None,
             "cycle": {k: cyc[k] for k in ("phase_cycle_pct", "multiple_mayer", "ecart_200_semaines_pct", "ecart_loi_puissance_pct",
                                         "ecart_ath_pct", "perf_7j_pct", "perf_30j_pct", "tendance_50_200")},
             "onchain_etf": [{k: v[k] for k in ("libelle", "z_1an", "tendance", "significatif", "suit_le_prix")} for v in oc["variables"][:10]],
             "fear_greed": se.get("fear_greed"), "previsions": {k: {kk: p[k][kk] for kk in ("date_cible", "cible", "bas_80", "haut_80", "confiance")}
                                                                for k in ("semaine", "mois")},
             "points_attention": pts,
             "niveaux_cles": [{k: l[k] for k in ("nom", "valeur", "date", "ecart_pct", "position")} for l in (niveaux or [])],
             "glassnode_market_pulse": {"publie_le": pulse["publie_le"], "categories": pulse["categories"],
                                        "extremes": pulse["extremes"], "resume": (pulse.get("resume") or {}).get("resume")}
             if pulse else None}
    redige = llm.weekly_narrative([n for n in news if n["date"] >= start], facts)
    return {"semaine": f"{today.isocalendar().year}-S{today.isocalendar().week:02d}", "genere_le": str(today.date()),
            "synthese_redigee": redige,
            "prix_reference": latest["prix_cloture_veille"],
            "previsions": {k: {kk: p[k][kk] for kk in ("date_cible", "cible", "bas_80", "haut_80", "variation_pct",
                                                     "confiance", "confiance_label", "signaux")} for k in ("semaine", "mois")},
            "points_attention": pts, "cycle": cyc, "niveaux_cles": niveaux or [],
            "glassnode_pulse": pulse,
            "regime": {k: regime[k] for k in ("score", "regime", "depuis", "jours_dans_regime", "composantes", "lecture")} if regime else None, "onchain": oc, "sentiment": se,
            "semaine_passee": past_week_perf(close, log, today), "qualite_donnees": latest["qualite_donnees"]}


def monthly(close, log, month_end):
    """Compare toutes les prévisions dont la date cible tombait dans le mois écoulé avec les prix réels."""
    if log is None or log.empty:
        return None
    l = log.copy()
    l["target_date"] = pd.to_datetime(l["target_date"])
    m0 = month_end.replace(day=1)
    l = l[(l.target_date >= m0) & (l.target_date <= month_end)]
    l["actual"] = l.target_date.map(close)
    l = l.dropna(subset=["actual"])
    if l.empty:
        return None
    stats = evaluate(l)
    # trajectoire des prévisions de fin de mois au fil des jours
    fm = l[l.horizon == "mois"].sort_values("run_date")
    traj = [{"date_prevision": r.run_date, "cible": r.q50, "bas_80": r.q10, "haut_80": r.q90} for r in fm.itertuples()]
    daily = l[l.horizon == "jour"].sort_values("target_date")
    det = [{"date": str(r.target_date.date()), "cible": r.q50, "bas_80": r.q10, "haut_80": r.q90, "reel": round(r.actual, 0),
            "erreur_pct": round(100 * (r.q50 / r.actual - 1), 2), "confiance": r.confidence} for r in daily.itertuples()]
    actual_end = float(close.get(month_end, np.nan))
    lines = []
    for k, s in sorted(stats.items(), key=lambda x: ["jour", "semaine", "mois"].index(x[0])):
        verdict = "meilleur" if (s["gain_vs_naif_pct"] or 0) > 0 else "moins bon"
        lines.append(f"Horizon {k} : erreur moyenne {s['mape_modele']} % contre {s['mape_naif']} % pour la prévision naïve "
                     f"(modèle {verdict}), direction juste {s['direction_ok_pct']} % du temps, "
                     f"{s['couverture_80_pct']} % des prix réels dans l'intervalle 80 %.")
    return {"mois": f"{month_end:%Y-%m}", "prix_fin_de_mois_reel": _f(actual_end, 0), "statistiques": stats,
            "trajectoire_prevision_fin_de_mois": traj, "detail_journalier": det, "lecture": lines}
