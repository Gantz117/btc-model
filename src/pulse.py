"""Lecture hebdomadaire du « BTC Market Pulse » de Glassnode (publié le lundi, ~16-18 h à Paris).

Le Pulse classe 21 indicateurs (dont plusieurs payants : options, CVD, profits/pertes) par rapport
à leurs bornes habituelles. On n'en garde que la POSITION de chaque indicateur (zone) et le sens de
sa variation sur 7 jours, plus un court résumé reformulé par Gemini : pas de republication des
chiffres ni du texte de Glassnode sur le tableau de bord public, seulement un lien vers l'article.

Ces informations servent de contexte (synthèse hebdo) et ne participent PAS à la prévision :
le format n'est stable que depuis octobre 2026, trop peu d'historique pour le valider."""
import html as htmlmod
import json
import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

import pandas as pd

from . import config as C
from . import llm

FEEDS = ["https://research.glassnode.com/rss/", "https://research.glassnode.com/tag/market-pulse/rss/"]
NS = {"content": "http://purl.org/rss/1.0/modules/content/"}
STATUS = {}

# nom Glassnode -> (nom français, catégorie)
INDICATORS = {
    "Price Momentum": ("Momentum du prix", "Spot"),
    "Spot CVD": ("Pression acheteuse au comptant (CVD)", "Spot"),
    "Spot Volume": ("Volume au comptant", "Spot"),
    "Futures Open Interest": ("Open interest des contrats à terme", "Contrats à terme"),
    "Long-Side Funding Payment": ("Financement payé par les acheteurs à levier", "Contrats à terme"),
    "Perpetual CVD": ("Pression acheteuse sur les perpétuels (CVD)", "Contrats à terme"),
    "Options Open Interest": ("Open interest des options", "Options"),
    "Volatility Spread": ("Écart de volatilité (implicite – réalisée)", "Options"),
    "Options 25-Delta Skew": ("Skew 25-delta (demande de protection)", "Options"),
    "US Spot ETF Netflow (Weekly)": ("Flux nets des ETF (semaine)", "ETF"),
    "US Spot ETF Trade Volume (Weekly)": ("Volume échangé des ETF (semaine)", "ETF"),
    "US Spot ETF MVRV": ("Profit latent des acheteurs d'ETF (MVRV)", "ETF"),
    "Total Fee Volume": ("Frais de transaction", "Activité du réseau"),
    "Entity-Adjusted Transfer Volume": ("Volume transféré (ajusté)", "Activité du réseau"),
    "Daily Active Address Count": ("Adresses actives", "Activité du réseau"),
    "Realized Cap Change (Monthly)": ("Entrées de capitaux (cap. réalisée, 30 j)", "Flux de capitaux"),
    "STH to LTH Supply Ratio": ("Offre détenteurs court terme / long terme", "Flux de capitaux"),
    "Hot Capital Share": ("Part des capitaux « chauds »", "Flux de capitaux"),
    "Percent Supply in Profit": ("Part de l'offre en profit", "Profits / pertes"),
    "Net Unrealized Profit to Loss Ratio": ("Profit latent net (NUPL)", "Profits / pertes"),
    "Realized Profit to Loss Ratio": ("Profits réalisés / pertes réalisées", "Profits / pertes"),
}
CATEGORIES = ["Spot", "Contrats à terme", "Options", "ETF", "Activité du réseau", "Flux de capitaux", "Profits / pertes"]

# zone -> score (-2 … +2) ; l'ordre de test compte (« well above » avant « above »)
ZONES = [
    (r"well above (its|the) high band", "très au-dessus de la borne haute", 2),
    (r"above (its|the) high band", "au-dessus de la borne haute", 2),
    (r"(top|upper part|upper third|upper end) of (its|the) range", "haut de la fourchette", 1),
    (r"middle of (its|the) range|mid-?range", "milieu de la fourchette", 0),
    (r"(bottom|lower part|lower third|lower end) of (its|the) range", "bas de la fourchette", -1),
    (r"well below (its|the) low band", "très en dessous de la borne basse", -2),
    (r"below (its|the) low band", "sous la borne basse", -2),
    (r"below (its|the) high band", "haut de la fourchette", 1),
    (r"above (its|the) low band", "bas de la fourchette", -1),
]


def _strip(h):
    return re.sub(r"\s+", " ", htmlmod.unescape(re.sub(r"<[^>]+>", " ", h or ""))).strip()


def zone_of(text):
    """Dernière zone mentionnée dans le paragraphe (l'état final de la semaine)."""
    found = [(m.start(), m.end(), label, score) for pat, label, score in ZONES for m in re.finditer(pat, text, re.I)]
    # on écarte une mention incluse dans une plus longue (« above its high band » dans « well above its high band »)
    found = [f for f in found if not any(g != f and g[0] <= f[0] and f[1] <= g[1] for g in found)]
    if not found:
        return "non précisée", None
    s, e, label, score = max(found, key=lambda f: f[0])  # la dernière mention = l'état en fin de semaine
    return label, score


def direction_of(text):
    t = text[:220].lower()
    if re.search(r"roughly (level|flat|unchanged)|\bunchanged\b|\bflat\b", t):
        return "stable"
    if re.search(r"\b(increased|rose|climbed|rebounded|jumped|grew|turned higher)\b", t):
        return "hausse"
    if re.search(r"\b(decreased|fell|declined|dropped|slipped|eased|turned lower)\b", t):
        return "baisse"
    return "?"


def parse_issue(content_html):
    """Renvoie la liste des indicateurs {nom, nom_fr, categorie, zone, score, direction} et le texte de l'aperçu."""
    out = []
    for m in re.finditer(r"<p[^>]*>\s*<strong>\s*([^<]+?)\s*\.?\s*</strong>(.*?)</p>", content_html, re.S | re.I):
        name = _strip(m.group(1)).rstrip(".")
        if name not in INDICATORS:
            continue
        text = _strip(m.group(2))
        zone, score = zone_of(text)
        fr, cat = INDICATORS[name]
        out.append({"nom": name, "nom_fr": fr, "categorie": cat, "zone": zone, "score": score,
                    "direction": direction_of(text)})
    ov = re.search(r"Overview\s*</h2>(.*?)<h2", content_html, re.S | re.I)
    overview = _strip(ov.group(1)) if ov else ""
    return out, overview


def summarize(indicators):
    cats = {}
    for c in CATEGORIES:
        sc = [i["score"] for i in indicators if i["categorie"] == c and i["score"] is not None]
        if not sc:
            continue
        m = sum(sc) / len(sc)
        cats[c] = {"niveau": "élevé" if m >= 0.75 else ("bas" if m <= -0.75 else "modéré"), "score": round(m, 2), "n": len(sc)}
    extremes = [{"nom_fr": i["nom_fr"], "categorie": i["categorie"], "zone": i["zone"], "direction": i["direction"]}
                for i in indicators if i["score"] in (2, -2)]
    return cats, extremes


def fetch_latest():
    best = None
    for url in FEEDS:
        try:
            import requests
            r = requests.get(url, headers=C.HTTP_HEADERS, timeout=30)
            r.raise_for_status()
            root = ET.fromstring(r.content)
            for it in root.iter("item"):
                title = (it.findtext("title") or "").strip()
                if not title.lower().startswith("btc market pulse"):
                    continue
                dt = parsedate_to_datetime(it.findtext("pubDate"))
                content = it.findtext("content:encoded", namespaces=NS) or it.findtext("description") or ""
                if best is None or dt > best["date"]:
                    best = {"titre": title, "lien": (it.findtext("link") or "").strip(), "date": dt, "html": content}
        except Exception as e:  # un flux en panne n'empêche pas l'autre
            STATUS.setdefault("erreurs", []).append(f"{url} : {str(e)[:100]}")
    return best


def from_inbox():
    """Numéro déposé depuis le PC de Federico (data/pulse_inbox.json) quand le flux RSS est bloqué pour
    les serveurs GitHub. Le fichier ne contient pas le texte de Glassnode : seulement, par indicateur,
    les expressions de position relevées (« below its low band »…) et le verbe de variation, plus un
    résumé rédigé en français lors de la lecture."""
    f = C.DATA / "pulse_inbox.json"
    if not f.exists():
        return None
    try:
        j = json.loads(f.read_text())
        inds = []
        for it in j.get("indicateurs", []):
            name = it.get("nom")
            if name not in INDICATORS:
                continue
            zone, score = zone_of(it.get("zones", ""))
            fr, cat = INDICATORS[name]
            inds.append({"nom": name, "nom_fr": fr, "categorie": cat, "zone": zone, "score": score,
                         "direction": direction_of(it.get("verbe", ""))})
        res = j.get("resume")
        return {"titre": j["titre"], "lien": j["lien"], "date": pd.Timestamp(j["publie_le"]),
                "inds": inds, "resume": res if isinstance(res, dict) else None, "via": "lecture depuis le PC"}
    except Exception as e:
        STATUS["inbox"] = f"fichier illisible : {str(e)[:120]}"
        return None


def update():
    """Récupère le dernier Pulse s'il est nouveau (flux RSS, sinon dépôt depuis le PC).
    Renvoie (pulse_dict ou None, nouveau: bool)."""
    out_f = C.OUT / "pulse_latest.json"
    prev = json.loads(out_f.read_text()) if out_f.exists() else None
    cands = []
    issue = fetch_latest()
    if issue:
        cands.append({"titre": issue["titre"], "lien": issue["lien"], "date": pd.Timestamp(issue["date"]).tz_localize(None)
                      if pd.Timestamp(issue["date"]).tzinfo else pd.Timestamp(issue["date"]), "html": issue["html"], "via": "flux RSS"})
    inbox = from_inbox()
    if inbox:
        cands.append(inbox)
    if not cands:
        STATUS.update(ok=False, raison="flux Glassnode indisponible et aucun dépôt depuis le PC")
        return prev, False
    best = max(cands, key=lambda c: c["date"])
    if prev and prev.get("lien") == best["lien"] and prev.get("indicateurs"):
        STATUS.update(ok=True, semaine=prev.get("titre"), nouveau=False, via=best["via"])
        return prev, False
    if "html" in best:
        inds, overview = parse_issue(best["html"])
        resume = None
    else:
        inds, overview, resume = best["inds"], "", best["resume"]
    if len(inds) < 12:  # format changé : on ne publie rien de douteux
        STATUS.update(ok=False, raison=f"format inattendu ({len(inds)} indicateurs reconnus sur {len(INDICATORS)})")
        return prev, False
    cats, extremes = summarize(inds)
    # tendance par catégorie : comparaison avec le numéro précédent
    if prev and prev.get("categories"):
        for c, v in cats.items():
            old = prev["categories"].get(c, {}).get("score")
            v["tendance"] = "—" if old is None else ("en hausse" if v["score"] - old > 0.25 else "en baisse" if old - v["score"] > 0.25 else "stable")
    if resume is None and overview:
        resume = llm.summarize_pulse(overview)
    pulse = {"titre": best["titre"], "lien": best["lien"], "publie_le": best["date"].strftime("%Y-%m-%d"),
             "categories": cats, "extremes": extremes,
             "indicateurs": [{k: i[k] for k in ("nom_fr", "categorie", "zone", "direction")} for i in inds],
             "resume": resume, "via": best["via"],
             "source": "Glassnode, BTC Market Pulse (lecture automatique, positions uniquement)"}
    out_f.write_text(json.dumps(pulse, ensure_ascii=False, indent=1))
    # historique des positions (sans les valeurs de Glassnode), pour juger dans un an ou deux
    hf = C.DATA / "pulse_zones.csv"
    rows = pd.DataFrame([{"publie_le": pulse["publie_le"], "indicateur": i["nom"], "zone": i["zone"], "score": i["score"],
                          "direction": i["direction"]} for i in inds])
    hist = pd.read_csv(hf) if hf.exists() else pd.DataFrame(columns=rows.columns)
    hist = pd.concat([hist[hist.publie_le != pulse["publie_le"]], rows]).sort_values(["publie_le", "indicateur"])
    hist.to_csv(hf, index=False)
    STATUS.update(ok=True, semaine=best["titre"], nouveau=True, indicateurs=len(inds), via=best["via"])
    return pulse, True
