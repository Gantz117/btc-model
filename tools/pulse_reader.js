// Lecture du dernier « BTC Market Pulse » de Glassnode depuis un navigateur (page research.glassnode.com).
// Renvoie, par indicateur, seulement les expressions de position (« below its low band »…) et le verbe de
// variation : ni valeurs ni texte de Glassnode. src/pulse.py en déduit la zone (dernière mention) et le sens.
// « apercu » sert uniquement à rédiger un résumé en français : il ne doit jamais être publié tel quel.
// Format de sortie = data/pulse_inbox.json (sans « apercu », avec « resume »).
(async () => {
  const ZONES = [  // mêmes motifs que src/pulse.py (le 2e et le 3e élément ne servent qu'à la lisibilité)
    [/well above (its|the) high band/gi, "très au-dessus de la borne haute", 2],
    [/above (its|the) high band/gi, "au-dessus de la borne haute", 2],
    [/(top|upper part|upper third|upper end) of (its|the) range/gi, "haut de la fourchette", 1],
    [/middle of (its|the) range|mid-?range/gi, "milieu de la fourchette", 0],
    [/(bottom|lower part|lower third|lower end) of (its|the) range/gi, "bas de la fourchette", -1],
    [/well below (its|the) low band/gi, "très en dessous de la borne basse", -2],
    [/below (its|the) low band/gi, "sous la borne basse", -2],
    [/below (its|the) high band/gi, "haut de la fourchette", 1],
    [/above (its|the) low band/gi, "bas de la fourchette", -1],
  ];
  // expressions de position, dans l'ordre du texte (on écarte celles incluses dans une plus longue)
  const zonePhrases = t => {
    let f = [];
    for (const [re] of ZONES) for (const m of t.matchAll(re)) f.push([m.index, m.index + m[0].length, m[0]]);
    f = f.filter(a => !f.some(b => b !== a && b[0] <= a[0] && a[1] <= b[1]));
    return f.sort((a, b) => a[0] - b[0]).map(x => x[2].toLowerCase()).join(" ; ");
  };
  // premier verbe de variation du paragraphe (« Increased by… », « Decreased by… », « roughly level »)
  const verbOf = t => {
    t = t.slice(0, 220).toLowerCase();
    const m = t.match(/roughly (level|flat|unchanged)|\bunchanged\b|\bflat\b|\b(increased|rose|climbed|rebounded|jumped|grew|decreased|fell|declined|dropped|slipped|eased)\b|turned (higher|lower)/);
    return m ? m[0] : "";
  };
  const NS = "http://purl.org/rss/1.0/modules/content/";
  let best = null;
  for (const url of ["/rss/", "/tag/market-pulse/rss/"]) {
    try {
      const xml = new DOMParser().parseFromString(await (await fetch(url, { cache: "no-store" })).text(), "application/xml");
      for (const it of xml.querySelectorAll("item")) {
        const titre = (it.querySelector("title")?.textContent || "").trim();
        if (!/^btc market pulse/i.test(titre)) continue;
        const d = new Date(it.querySelector("pubDate").textContent);
        if (!best || d > best.d) best = { d, titre, lien: it.querySelector("link").textContent.trim(),
          html: it.getElementsByTagNameNS(NS, "encoded")[0]?.textContent || "" };
      }
    } catch (e) { /* l'autre flux peut suffire */ }
  }
  if (!best) return JSON.stringify({ erreur: "aucun BTC Market Pulse trouvé dans les flux" });
  const doc = new DOMParser().parseFromString(best.html, "text/html");
  const indicateurs = [];
  for (const p of doc.querySelectorAll("p")) {
    const s = p.querySelector("strong");
    if (!s) continue;
    const nom = s.textContent.trim().replace(/\.$/, "");
    const texte = p.textContent.replace(s.textContent, "").replace(/\s+/g, " ").trim();
    if (!/seven days ago|compared to|roughly level/i.test(texte)) continue;
    indicateurs.push({ nom, zones: zonePhrases(texte), verbe: verbOf(texte) });
  }
  let apercu = "";
  const h = [...doc.querySelectorAll("h2")].find(x => /overview/i.test(x.textContent));
  for (let n = h?.nextElementSibling; n && n.tagName !== "H2"; n = n.nextElementSibling) apercu += n.textContent.trim() + "\n";
  return JSON.stringify({ titre: best.titre, lien: best.lien, publie_le: best.d.toISOString().slice(0, 10), indicateurs, apercu: apercu.trim() });
})()
