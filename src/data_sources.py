"""Collecte des données gratuites : prix, on-chain, sentiment, actualités.
Chaque source est tolérante aux pannes : en cas d'erreur, on garde le cache et on le signale."""
import json
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime

import numpy as np
import pandas as pd
import requests

from . import config as C

STATUS = {}  # état de chaque source pour le tableau de bord


def _get(url, params=None, timeout=40, retries=2):
    last = None
    for i in range(retries + 1):
        try:
            r = requests.get(url, params=params, headers=C.HTTP_HEADERS, timeout=timeout)
            if r.status_code == 429:
                raise RuntimeError("limite de requêtes atteinte (429)")
            if r.status_code in (401, 403):
                raise PermissionError(f"accès refusé ({r.status_code})")
            r.raise_for_status()
            return r
        except PermissionError:
            raise
        except Exception as e:  # noqa
            last = e
            time.sleep(2 * (i + 1))
    raise last


# ---------------------------------------------------------------- prix + CoinMetrics
CM_METRICS = ["PriceUSD", "CapMVRVCur", "FlowInExNtv", "FlowOutExNtv", "SplyExNtv",
              "SplyCur", "AdrActCnt", "AdrBalCnt", "TxTfrCnt", "HashRate"]
CM_URL = "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"


def _cm_fetch(metrics, start):
    rows, url = [], CM_URL
    params = {"assets": "btc", "metrics": ",".join(metrics), "frequency": "1d",
              "start_time": start, "page_size": 10000}
    while url:
        j = _get(url, params=params).json()
        rows += j.get("data", [])
        url, params = j.get("next_page_url"), None
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["time"] = pd.to_datetime(df["time"].str[:10])
    df = df.set_index("time")[[m for m in metrics if m in df.columns]]  # ignore les colonnes « -status »
    return df.apply(pd.to_numeric, errors="coerce")


def load_coinmetrics():
    seed = pd.read_csv(C.SEED / "coinmetrics_btc.csv", parse_dates=["time"]).set_index("time")
    cache_f = C.DATA / "coinmetrics_live.csv"
    live = pd.read_csv(cache_f, parse_dates=["time"]).set_index("time") if cache_f.exists() else pd.DataFrame()
    base = pd.concat([seed, live])
    base = base[~base.index.duplicated(keep="last")].sort_index()
    start = (base.index.max() - timedelta(days=5)).strftime("%Y-%m-%d")
    try:
        try:
            new = _cm_fetch(CM_METRICS, start)
        except PermissionError:  # une métrique est devenue payante : on les prend une par une
            parts = []
            for m in CM_METRICS:
                try:
                    parts.append(_cm_fetch([m], start))
                except Exception:
                    pass
            new = pd.concat(parts, axis=1) if parts else pd.DataFrame()
        if len(new):
            live = pd.concat([live, new])
            live = live[~live.index.duplicated(keep="last")].sort_index()
            live.index.name = "time"
            live.to_csv(cache_f, float_format="%.8g")
        STATUS["coinmetrics"] = {"ok": True, "last": str(new.index.max().date()) if len(new) else None}
    except Exception as e:
        STATUS["coinmetrics"] = {"ok": False, "error": str(e)[:200]}
    df = pd.concat([seed, live])
    df = df[~df.index.duplicated(keep="last")].sort_index()
    return df


def load_kraken():
    """Bougies journalières (UTC) des ~720 derniers jours + prix instantané."""
    out = {"ohlc": pd.DataFrame(), "spot": None}
    try:
        j = _get("https://api.kraken.com/0/public/OHLC", {"pair": "XBTUSD", "interval": 1440}).json()
        key = [k for k in j["result"] if k != "last"][0]
        a = pd.DataFrame(j["result"][key], columns=["t", "open", "high", "low", "close", "vwap", "vol", "n"])
        a["time"] = pd.to_datetime(a["t"].astype(int), unit="s")
        a = a.set_index("time")[["open", "high", "low", "close", "vol"]].astype(float)
        out["ohlc"] = a
        t = _get("https://api.kraken.com/0/public/Ticker", {"pair": "XBTUSD"}).json()
        out["spot"] = float(list(t["result"].values())[0]["c"][0])
        STATUS["kraken"] = {"ok": True, "spot": out["spot"]}
    except Exception as e:
        STATUS["kraken"] = {"ok": False, "error": str(e)[:200]}
    return out


def build_price(cm, kr):
    """Série de clôtures journalières UTC. Le jour en cours (incomplet) est exclu."""
    today = pd.Timestamp(datetime.now(timezone.utc).date())
    close = cm["PriceUSD"].dropna().copy()
    if len(kr["ohlc"]):
        k = kr["ohlc"]["close"]
        k = k[k.index < today]
        close = pd.concat([close[close.index < k.index.min()], k])
    close = close[close.index < today]
    close = close[~close.index.duplicated(keep="last")].sort_index()
    return close.asfreq("D").ffill()


# ---------------------------------------------------------------- BGeometrics (whales, cohortes)
def load_bgeometrics():
    out = {}
    today = pd.Timestamp(datetime.now(timezone.utc).date())
    calls = 0
    log_f = C.DATA / "bgeo_fetch.json"           # date du dernier appel réussi par série
    fetched = json.loads(log_f.read_text()) if log_f.exists() else {}
    for ep, name in C.BGEO_SERIES.items():
        f = C.DATA / f"bgeo_{name}.csv"
        cached = pd.read_csv(f, parse_dates=["time"]).set_index("time")["value"] if f.exists() else None
        last_call = pd.Timestamp(fetched[name]) if name in fetched else None
        fresh = cached is not None and last_call is not None and \
            (today - last_call).days < getattr(C, "BGEO_MAX_AGE", {}).get(name, 1)
        if not fresh and calls < getattr(C, "BGEO_MAX_CALLS", 8):
            calls += 1
            try:
                j = _get(f"https://bitcoin-data.com/v1/{ep}", retries=0).json()
                if isinstance(j, dict):
                    j = [j]
                df = pd.DataFrame(j)
                cands = [c for c in df.columns if c not in ("d", "unixTs", "delayed", "message")]
                field = max(cands, key=lambda c: pd.to_numeric(df[c], errors="coerce").notna().sum())
                s = pd.Series(pd.to_numeric(df[field], errors="coerce").values,
                              index=pd.to_datetime(df["d"].astype(str).str[:10]), name="value").dropna()
                s = s.groupby(level=0).sum() if name in C.BGEO_SUM else s.groupby(level=0).mean()
                if cached is not None:
                    s = pd.concat([cached, s])
                    s = s[~s.index.duplicated(keep="last")]
                s = s.sort_index()
                s.index.name = "time"
                s.to_frame().to_csv(f)
                cached = s
                fetched[name] = str(today.date())
                log_f.write_text(json.dumps(fetched, indent=0))
                STATUS[f"bgeo:{name}"] = {"ok": True, "last": str(s.index.max().date())}
            except Exception as e:
                STATUS[f"bgeo:{name}"] = {"ok": False, "error": str(e)[:150], "cache": cached is not None}
            time.sleep(1.5)
        elif cached is not None:
            STATUS[f"bgeo:{name}"] = {"ok": True, "last": str(cached.index.max().date()), "cache": True}
        if cached is not None and len(cached):
            out[name] = cached
    return pd.DataFrame(out)


# ---------------------------------------------------------------- Fear & Greed
def load_fear_greed():
    f = C.DATA / "fear_greed.csv"
    try:
        j = _get("https://api.alternative.me/fng/", {"limit": 0}).json()
        s = pd.Series({pd.to_datetime(int(d["timestamp"]), unit="s"): float(d["value"]) for d in j["data"]})
        s = s.sort_index()
        s.index.name = "time"
        s.rename("value").to_frame().to_csv(f)
        STATUS["fear_greed"] = {"ok": True, "last": str(s.index.max().date()), "value": s.iloc[-1]}
    except Exception as e:
        STATUS["fear_greed"] = {"ok": False, "error": str(e)[:200]}
        if not f.exists():
            return pd.Series(dtype=float)
        s = pd.read_csv(f, parse_dates=["time"]).set_index("time")["value"]
    return s


# ---------------------------------------------------------------- Actualités (RSS) + sentiment
POS = {"surge": 2, "surges": 2, "soar": 2, "soars": 2, "rally": 2, "rallies": 2, "record": 1.5, "high": 0.5,
       "highs": 1, "bull": 1.5, "bullish": 2, "gain": 1, "gains": 1, "jump": 1.5, "jumps": 1.5, "rise": 1,
       "rises": 1, "climb": 1, "climbs": 1, "inflow": 1.5, "inflows": 1.5, "approve": 2, "approved": 2,
       "approval": 2, "adoption": 1.5, "adopt": 1, "buy": 1, "buys": 1, "bought": 1, "accumulate": 1.5,
       "accumulation": 1.5, "accumulating": 1.5, "breakout": 1.5, "recover": 1, "recovers": 1, "rebound": 1.5,
       "support": 0.5, "strong": 1, "optimism": 1.5, "upgrade": 1, "reserve": 0.5, "cut": 0.5, "cuts": 0.5,
       "ease": 0.5, "boost": 1.5, "boosts": 1.5, "milestone": 1, "outperform": 1, "green": 0.5}
NEG = {"crash": 2.5, "crashes": 2.5, "plunge": 2, "plunges": 2, "dump": 2, "dumps": 2, "drop": 1, "drops": 1,
       "fall": 1, "falls": 1, "slump": 2, "slumps": 2, "bear": 1.5, "bearish": 2, "sell-off": 2, "selloff": 2,
       "outflow": 1.5, "outflows": 1.5, "hack": 2.5, "hacked": 2.5, "exploit": 2, "ban": 2, "bans": 2,
       "crackdown": 2, "lawsuit": 1.5, "sue": 1.5, "sues": 1.5, "fraud": 2, "liquidation": 1.5,
       "liquidations": 1.5, "fear": 1.5, "fears": 1.5, "risk": 0.5, "warning": 1, "warns": 1, "reject": 1.5,
       "rejected": 1.5, "delay": 1, "delays": 1, "decline": 1, "declines": 1, "loss": 1, "losses": 1.5,
       "weak": 1, "tumble": 2, "tumbles": 2, "sell": 1, "sells": 1, "selling": 1, "hike": 1, "hikes": 1,
       "tariff": 1, "tariffs": 1, "recession": 1.5, "bankrupt": 2.5, "bankruptcy": 2.5, "collapse": 2.5, "red": 0.5}
NEGATORS = {"not", "no", "never", "without", "fails", "failed", "despite"}
TAGS = {
    "ETF": r"\betfs?\b|blackrock|ibit|fidelity|grayscale",
    "Régulation": r"\bsec\b|regulat|cftc|\bmica\b|lawmaker|congress|senate|bill\b|legal|court|ban\b",
    "Macro / Fed": r"\bfed\b|powell|rate (cut|hike)|inflation|\bcpi\b|treasur|yields?|dollar|tariff|recession|jobs report",
    "Institutionnels": r"strategy|microstrategy|saylor|treasury compan|institution|corporate|bank",
    "Whales": r"whale|large holder|long-term holder|\blth\b|exchange (reserve|outflow|inflow)",
    "Mining": r"miner|mining|hashrate|hash rate|difficulty",
    "Sécurité": r"hack|exploit|stolen|breach|scam",
    "Dérivés": r"futures|options|funding|liquidat|open interest|leverage",
}


def score_text(text):
    words = re.findall(r"[a-z][a-z\-']+", text.lower())
    s, n = 0.0, 0
    for i, w in enumerate(words):
        v = POS.get(w, 0) - NEG.get(w, 0)
        if v:
            if any(x in NEGATORS for x in words[max(0, i - 3):i]):
                v = -v * 0.7
            s += v
            n += 1
    return float(np.tanh(s / 3.0)) if n else 0.0


def _strip(html):
    return re.sub(r"<[^>]+>", " ", html or "").replace("&nbsp;", " ").strip()


def load_news():
    items = []
    ok = 0
    for url in C.NEWS_FEEDS:
        try:
            root = ET.fromstring(_get(url, timeout=25, retries=1).content)
            ok += 1
            for it in root.iter("item"):
                title = (it.findtext("title") or "").strip()
                if not title:
                    continue
                link = (it.findtext("link") or "").strip()
                desc = _strip(it.findtext("description"))[:400]
                try:
                    dt = parsedate_to_datetime(it.findtext("pubDate")).astimezone(timezone.utc)
                except Exception:
                    dt = datetime.now(timezone.utc)
                src = re.sub(r"^https?://(www\.)?", "", url).split("/")[0]
                text = f"{title}. {desc}"
                if "bitcoin" not in text.lower() and "btc" not in text.lower() and "crypto" not in text.lower():
                    continue
                items.append({"date": dt.strftime("%Y-%m-%d"), "ts": dt.isoformat(), "title": title, "link": link,
                              "source": src, "score": round(score_text(text), 3),
                              "tags": [k for k, p in TAGS.items() if re.search(p, text.lower())]})
        except Exception:
            pass
    STATUS["news"] = {"ok": ok > 0, "feeds_ok": ok, "feeds": len(C.NEWS_FEEDS), "items": len(items)}
    # journal persistant des titres (sert à la synthèse hebdomadaire). Les titres déjà connus
    # sont gardés tels quels, avec leur éventuelle note Gemini.
    f = C.DATA / "news_items.json"
    old = json.loads(f.read_text()) if f.exists() else []
    seen, merged = set(), []
    for it in old + items:
        k = it["title"].lower()[:90]
        if k not in seen:
            seen.add(k)
            merged.append(it)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=45)).strftime("%Y-%m-%d")
    merged = sorted([m for m in merged if m["date"] >= cutoff], key=lambda m: m["ts"], reverse=True)
    # analyse Gemini des titres des 2 derniers jours (sinon : note du lexique)
    from . import llm
    recent_cut = (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%d")
    day_summary = llm.score_headlines([m for m in merged if m["date"] >= recent_cut])
    for m in merged:
        m["score_lexique"] = m.get("score_lexique", m["score"])
        if "llm_impact" in m:
            m["score"] = m["llm_impact"]
            if m.get("llm_theme") and m["llm_theme"] != "Autre" and m["llm_theme"] not in m["tags"]:
                m["tags"] = [m["llm_theme"]] + m["tags"]
    STATUS["gemini"] = dict(llm.STATUS)
    if day_summary:
        (C.OUT / "news_today.json").write_text(json.dumps({"date": datetime.now(timezone.utc).strftime("%Y-%m-%d"), **day_summary},
                                                          ensure_ascii=False, indent=1))
    f.write_text(json.dumps(merged, ensure_ascii=False, indent=0))
    # journal quotidien agrégé (historique de sentiment qui s'accumule dans le temps).
    # score_ia = moyenne des impacts Gemini pondérée par l'importance ; score = lexique.
    lf = C.DATA / "news_daily.csv"
    log = pd.read_csv(lf) if lf.exists() else pd.DataFrame(columns=["date", "n", "score", "score_ia"])
    if merged:
        df = pd.DataFrame(merged)
        agg = df.groupby("date").agg(n=("title", "count"), score=("score_lexique", "mean")).reset_index()
        if "llm_impact" in df:
            g = df.dropna(subset=["llm_impact"])
            if len(g):
                w = g["llm_importance"].clip(lower=0.05)
                ia = (g["llm_impact"] * w).groupby(g["date"]).sum() / w.groupby(g["date"]).sum()
                agg["score_ia"] = agg["date"].map(ia)
        old_ia = log.set_index("date")["score_ia"] if "score_ia" in log else pd.Series(dtype=float)
        if "score_ia" not in agg:
            agg["score_ia"] = np.nan
        agg["score_ia"] = agg["score_ia"].fillna(agg["date"].map(old_ia))   # ne pas effacer une note passée
        log = pd.concat([log[~log.date.isin(agg.date)], agg]).sort_values("date")
        log.to_csv(lf, index=False, float_format="%.4f")
    return merged
