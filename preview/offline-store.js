/**
 * Offline API responder for the STANDALONE PREVIEW bundle only (not shipped in
 * web/). Answers the app's API calls from snapshot JSON embedded in the bundle
 * so the preview runs with no server. The shipped website never loads this.
 * Data is provided as <script type="application/json"> blocks with ids
 * offline-articles / offline-itm / offline-manifest.
 */
(() => {
  let articles = [];
  let itm = null;
  let manifest = null;

  function embeddedJson(id) {
    const el = document.getElementById(id);
    if (!el) return null;
    try {
      return JSON.parse(el.textContent);
    } catch {
      return null;
    }
  }

  function aliasMatches(phrase, haystack) {
    if (!phrase || phrase.length < 3) return false;
    if (!haystack.includes(phrase)) return false;
    if (phrase.includes(" ")) return true;
    const re = new RegExp(`(?:^|[^a-z0-9])${phrase.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}(?:[^a-z0-9]|$)`, "i");
    return re.test(haystack);
  }

  /** Keep in sync with high-signal gaps in shared/itm/aliases.py (demo snapshot lag). */
  const DEMO_ALIAS_EXTRAS = {
    IF038: [
      "overemployment",
      "over-employment",
      "over employed",
      "overemployed",
      "moonlighting",
      "moonlight",
      "moonlighting policy",
      "side job",
      "side hustle",
      "second job",
      "dual employment",
      "concurrent employment",
      "undisclosed employment",
      "undisclosed concurrent employment",
      "secret second job",
      "multiple jobs",
      "working two jobs",
      "two jobs",
      "outside employment",
      "outside employment policy",
      "outside employment disclosure",
      "conflict of interest disclosure",
      "coi disclosure",
      "secondary employment",
      "additional employment",
      "J2",
      "job 2",
    ],
  };

  function techById() {
    const map = {};
    (itm.techniques || []).forEach((t) => {
      const id = String(t.id).toUpperCase();
      const extras = DEMO_ALIAS_EXTRAS[id] || [];
      if (!extras.length) {
        map[id] = t;
        return;
      }
      const aliases = [...(t.aliases || [])];
      const seen = new Set(aliases.map((a) => String(a).toLowerCase()));
      extras.forEach((a) => {
        const key = String(a).toLowerCase();
        if (!seen.has(key)) {
          seen.add(key);
          aliases.push(a);
        }
      });
      map[id] = { ...t, aliases };
    });
    return map;
  }

  function enrichItmCatalog(params) {
    const sourceId = params.source_id || "";
    const channel = params.channel || "all";
    const base = itm || { techniques: [], detections: [], preventions: [], articles: [] };
    const byId = techById();
    const techniques = (base.techniques || []).map((t) => {
      const enriched = byId[String(t.id).toUpperCase()] || t;
      const needle = String(t.id).toUpperCase();
      const article_count = articles.filter((article) => {
        if (sourceId && article.source_id !== sourceId) return false;
        if (!articleMatchesChannel(article, channel)) return false;
        const hits = article.itm_hits || [];
        return hits.some(
          (h) =>
            String(h.id).toUpperCase() === needle ||
            String(h.id).toUpperCase().startsWith(`${needle}.`),
        );
      }).length;
      return { ...enriched, article_count };
    });
    return { ...base, techniques };
  }

  function articleMatchesAlignment(article, mode) {
    const m = (mode || "insider").toLowerCase();
    if (m === "all" || m === "*" || m === "") return true;
    return (article.itm_alignment || "weak") === "insider";
  }

  function articleChannel(article) {
    const ch = String(article.channel || "").toLowerCase();
    if (ch === "news" || ch === "filings" || ch === "tips" || ch === "social") return ch;
    const sid = String(article.source_id || "").toLowerCase();
    if (sid.startsWith("social-")) return "social";
    if (sid.startsWith("reddit-") || sid.startsWith("tip-")) return "tips";
    if (sid.includes("courtlistener")) return "filings";
    return "news";
  }

  function articleMatchesChannel(article, mode) {
    const m = (mode || "all").toLowerCase();
    if (m === "all" || m === "*" || m === "") return true;
    return articleChannel(article) === m;
  }

  function articleMatchesUseCase(article, mode) {
    const m = (mode || "all").toLowerCase();
    if (m === "all" || m === "*" || m === "") return true;
    return (article.use_cases || []).includes(m);
  }

  function articleMatchesInsiderType(article, mode) {
    const m = (mode || "all").toLowerCase();
    if (m === "all" || m === "*" || m === "") return true;
    const value = article.insider_type || null;
    if (m === "none" || m === "unclassified") return value === null;
    return value === m;
  }

  function articleMatchesTopic(article, tech) {
    const blob = (article.topic_blob || `${article.title || ""} ${article.summary || ""}`).toLowerCase();
    const phrases = [];
    if (tech.title) phrases.push(String(tech.title).toLowerCase());
    (tech.aliases || []).forEach((a) => {
      const c = String(a || "").trim().toLowerCase();
      if (c) phrases.push(c);
    });
    phrases.sort((a, b) => b.length - a.length);
    return phrases.some((p) => aliasMatches(p, blob));
  }

  function articleMatchesItmId(article, itmId, topicMatch) {
    const needle = String(itmId || "").trim().toUpperCase();
    if (!needle) return true;
    const hits = article.itm_hits || [];
    if (
      hits.some(
        (h) =>
          String(h.id).toUpperCase() === needle ||
          String(h.id).toUpperCase().startsWith(`${needle}.`),
      )
    ) {
      return true;
    }
    if (!topicMatch) return false;
    const tech = techById()[needle];
    if (!tech) return false;
    return articleMatchesTopic(article, tech);
  }

  function techniquesForControl(kind, controlId) {
    const needle = String(controlId || "").toUpperCase();
    return (itm.techniques || [])
      .filter((t) => {
        const list = kind === "prevention" ? t.preventions || [] : t.detections || [];
        return list.some((c) => String(c.id).toUpperCase() === needle);
      })
      .map((t) => t.id);
  }

  function articleMatchesControl(article, kind, controlId, topicMatch) {
    const techIds = techniquesForControl(kind, controlId);
    if (!techIds.length) return false;
    return techIds.some((id) => articleMatchesItmId(article, id, topicMatch));
  }

  function normalizeTitle(title) {
    let text = String(title || "")
      .trim()
      .toLowerCase();
    for (let i = 0; i < 2; i += 1) {
      const next = text.replace(/\s*[|\u2013\u2014\-]\s*[^|]+$/u, "").trim();
      if (next === text) break;
      text = next;
    }
    return text
      .replace(/[^a-z0-9\s]+/g, " ")
      .replace(/\s+/g, " ")
      .trim();
  }

  function storyDay(published) {
    if (!published) return "unknown";
    const d = new Date(published);
    if (Number.isNaN(d.getTime())) return "unknown";
    return d.toISOString().slice(0, 10);
  }

  function computeStoryKey(article) {
    if (article.story_key) return String(article.story_key);
    const payload = `${normalizeTitle(article.title)}|${storyDay(article.published)}`;
    // FNV-1a 32-bit hex — stable enough for demo clustering
    let h = 0x811c9dc5;
    for (let i = 0; i < payload.length; i += 1) {
      h ^= payload.charCodeAt(i);
      h = Math.imul(h, 0x01000193);
    }
    return (h >>> 0).toString(16).padStart(8, "0");
  }

  function isRedditish(sourceId) {
    const sid = String(sourceId || "").toLowerCase();
    return sid.startsWith("reddit-") || sid.startsWith("tip-");
  }

  function pickPrimary(members) {
    return members.slice().sort((a, b) => {
      const rs = (b.relevance_score || 0) - (a.relevance_score || 0);
      if (rs) return rs;
      const da = a.published || "";
      const db = b.published || "";
      if (da !== db) return da < db ? 1 : -1;
      return (isRedditish(a.source_id) ? 1 : 0) - (isRedditish(b.source_id) ? 1 : 0);
    })[0];
  }

  function clusterRows(rows) {
    const buckets = new Map();
    rows.forEach((a) => {
      const key = computeStoryKey(a);
      const channel = articleChannel(a);
      const bucket = `${channel}:${key}`;
      if (!a.story_key) a.story_key = key;
      if (!buckets.has(bucket)) buckets.set(bucket, []);
      buckets.get(bucket).push(a);
    });
    const clusters = [];
    buckets.forEach((members) => {
      const primary = pickPrimary(members);
      const siblings = members
        .filter((m) => m.link !== primary.link)
        .sort((a, b) =>
          String(a.source_name).localeCompare(String(b.source_name)) ||
          String(a.link).localeCompare(String(b.link)),
        );
      clusters.push({
        story_key: computeStoryKey(primary),
        channel: articleChannel(primary),
        primary,
        siblings,
        member_count: members.length,
      });
    });
    clusters.sort((a, b) => {
      const da = a.primary.published || "";
      const db = b.primary.published || "";
      if (da !== db) return da < db ? 1 : -1;
      return (b.primary.relevance_score || 0) - (a.primary.relevance_score || 0);
    });
    return clusters;
  }

  function filterArticles(params) {
    const minScore = Number(params.min_score ?? 0.15);
    const sourceId = params.source_id || "";
    const theme = (params.theme || "").toLowerCase();
    const itmId = params.itm_id || "";
    const detectionId = params.detection_id || "";
    const preventionId = params.prevention_id || "";
    const topicMatch =
      params.topic_match === true ||
      params.topic_match === "true" ||
      params.topic_match === "1";
    const alignment = params.itm_alignment || "insider";
    const channel = params.channel || "all";
    const limit = Math.min(Number(params.limit || 50), 200);
    const group =
      params.group === undefined ||
      params.group === true ||
      params.group === "true" ||
      params.group === "1" ||
      params.group === 1;

    let rows = articles.filter((a) => {
      // Publications are exempt from the relevance floor (API parity).
      if ((a.relevance_score ?? 0) < minScore && articleChannel(a) !== "publications") {
        return false;
      }
      if (sourceId && a.source_id !== sourceId) return false;
      if (!articleMatchesAlignment(a, alignment)) return false;
      if (!articleMatchesChannel(a, channel)) return false;
      if (!articleMatchesUseCase(a, params.use_case)) return false;
      if (!articleMatchesInsiderType(a, params.insider_type)) return false;
      if (theme) {
        const hits = a.itm_hits || [];
        if (!hits.some((h) => String(h.theme || "").toLowerCase() === theme)) return false;
      }
      if (itmId && !articleMatchesItmId(a, itmId, topicMatch)) return false;
      if (detectionId && !articleMatchesControl(a, "detection", detectionId, topicMatch)) {
        return false;
      }
      if (
        preventionId &&
        !articleMatchesControl(a, "prevention", preventionId, topicMatch)
      ) {
        return false;
      }
      return true;
    });

    if (group) {
      const clusters = clusterRows(rows).slice(0, limit);
      return {
        total_indexed: articles.length,
        count: clusters.length,
        results: clusters.map((c) => c.primary),
        clusters,
      };
    }

    rows.sort((a, b) => {
      const da = a.published || "";
      const db = b.published || "";
      return da < db ? 1 : da > db ? -1 : 0;
    });
    rows = rows.slice(0, limit);
    return {
      total_indexed: articles.length,
      count: rows.length,
      results: rows,
      clusters: [],
    };
  }

  function listSources(params) {
    const filtered = filterArticles({
      ...params,
      limit: 500,
      min_score: params.min_score ?? 0.15,
      group: false,
    }).results;
    const map = new Map();
    filtered.forEach((a) => {
      const cur = map.get(a.source_id) || {
        id: a.source_id,
        name: a.source_name,
        article_count: 0,
      };
      cur.article_count += 1;
      map.set(a.source_id, cur);
    });
    return Array.from(map.values()).sort((a, b) => a.name.localeCompare(b.name));
  }

  function search(params) {
    const q = String(params.q || "").trim().toLowerCase();
    const tokens = q.split(/\s+/).filter(Boolean);
    const base = filterArticles({
      ...params,
      limit: 500,
      itm_id: "",
      detection_id: "",
      prevention_id: "",
      topic_match: false,
    }).results;
    if (!tokens.length) {
      return {
        query: q,
        mode: params.mode || "hybrid",
        total_indexed: articles.length,
        count: 0,
        results: [],
      };
    }
    const scored = base
      .map((a) => {
        const hay = `${a.title || ""} ${a.summary || ""} ${(a.keywords_hit || []).join(" ")} ${(a.operator_terms || []).join(" ")}`.toLowerCase();
        const hits = tokens.filter((t) => hay.includes(t)).length;
        const score = hits / tokens.length;
        return { article: a, score };
      })
      .filter((x) => x.score > 0)
      .sort((a, b) => b.score - a.score)
      .slice(0, Number(params.limit || 40));
    return {
      query: q,
      mode: params.mode || "hybrid",
      total_indexed: articles.length,
      count: scored.length,
      results: scored.map((x) => ({ ...x.article, score: Number(x.score.toFixed(4)) })),
    };
  }

  // Mirrors ArticleSearchIndex.trending: recent vs prior window over the
  // snapshot, anchored to the newest published stamp (static data). The
  // snapshot carries no use_cases field, so topics here are ITM parent
  // techniques + matched terms.
  function trending(params) {
    const windowDays = Math.min(30, Math.max(1, Number(params.window_days || 7)));
    const limit = Math.min(20, Math.max(1, Number(params.limit || 8)));
    const stamps = articles
      .map((a) => new Date(a.published || 0).getTime())
      .filter((t) => Number.isFinite(t) && t > 0);
    if (!stamps.length) return { window_days: windowDays, items: [] };
    const anchor = Math.max(...stamps);
    // Terms that merely restate a technique title add no signal (API parity).
    const redundantTerms = new Set(
      ((itm && itm.techniques) || []).map((t) => String(t.title || "").toLowerCase()),
    );
    const windowMs = windowDays * 86400000;
    const recentStart = anchor - windowMs;
    const priorStart = anchor - 2 * windowMs;

    const topics = new Map();
    const touch = (kind, key, label, bucket, story, channel) => {
      const id = `${kind}|${key}`;
      let topic = topics.get(id);
      if (!topic) {
        topic = { kind, key, label, recent: new Set(), prior: new Set(), channels: {} };
        topics.set(id, topic);
      }
      topic[bucket].add(story);
      if (bucket === "recent") {
        topic.channels[channel] = (topic.channels[channel] || 0) + 1;
      }
    };

    articles.forEach((a) => {
      const t = new Date(a.published || 0).getTime();
      if (!Number.isFinite(t) || t < priorStart) return;
      const bucket = t >= recentStart ? "recent" : "prior";
      const story = a.story_key || a.link;
      const channel = articleChannel(a);
      const seen = new Set();
      const terms = new Set();
      (a.itm_hits || []).forEach((hit) => {
        const pid = String(hit.id || "").toUpperCase().split(".")[0];
        if (pid && !seen.has(pid)) {
          seen.add(pid);
          touch("technique", pid, hit.title || pid, bucket, story, channel);
        }
        (hit.matched_aliases || []).forEach((al) => terms.add(String(al).toLowerCase()));
      });
      (a.keywords_hit || []).forEach((kw) => terms.add(String(kw).toLowerCase()));
      terms.forEach((term) => {
        // Bare taxonomy ids ("me024") read as noise in trending terms.
        if (
          term.length >= 3 &&
          !redundantTerms.has(term) &&
          !/^[a-z]{2}\d{3}(\.\d+)?$/.test(term)
        ) {
          touch("term", term, term, bucket, story, channel);
        }
      });
    });

    const items = [];
    topics.forEach((topic) => {
      const count = topic.recent.size;
      const prev = topic.prior.size;
      const floor = topic.kind === "term" ? 3 : 2;
      if (count < floor) return;
      let deltaPct = null;
      let direction = "new";
      if (prev > 0) {
        deltaPct = Math.round(((count - prev) / prev) * 1000) / 10;
        direction = deltaPct > 0 ? "up" : deltaPct < 0 ? "down" : "flat";
      }
      const channel =
        Object.entries(topic.channels).sort((x, y) => y[1] - x[1]).map((e) => e[0])[0] ||
        "news";
      items.push({
        kind: topic.kind,
        key: topic.key,
        label: topic.label,
        channel,
        count,
        prev_count: prev,
        delta_pct: deltaPct,
        direction,
      });
    });
    items.sort((x, y) => {
      const rank = (i) => (i.direction === "new" ? Infinity : i.delta_pct || 0);
      return rank(y) - rank(x) || y.count - x.count || x.label.localeCompare(y.label);
    });
    return { window_days: windowDays, items: items.slice(0, limit) };
  }

  const ready = (async () => {
    const arts = embeddedJson("offline-articles") || {};
    articles = arts.articles || arts.results || [];
    itm = embeddedJson("offline-itm");
    manifest = embeddedJson("offline-manifest");
  })();

  window.InsiderIntelOffline = {
    ready,
    async request(path, params = {}, options = {}) {
      await ready;
      const method = (options.method || "GET").toUpperCase();
      if (path === "/health") {
        return {
          status: "ok",
          demo: true,
          indexed_articles: articles.length,
          generated_at: manifest && manifest.generated_at,
        };
      }
      if (path === "/itm") return enrichItmCatalog(params);
      if (path === "/sources") return listSources(params);
      if (path === "/trending") return trending(params);
      if (path === "/articles") return filterArticles(params);
      if (path === "/search") return search(params);
      if (path === "/reload" && method === "POST") {
        return { ok: true, demo: true, indexed_articles: articles.length };
      }
      if (path === "/articles/by-links" && method === "POST") {
        let links = [];
        try {
          const raw = options.body ? JSON.parse(options.body) : {};
          links = Array.isArray(raw.links) ? raw.links : [];
        } catch {
          links = [];
        }
        const results = links
          .map((link) => articles.find((a) => a.link === link))
          .filter(Boolean);
        const found = new Set(results.map((a) => a.link));
        return {
          results,
          missing: links.filter((link) => !found.has(link)),
        };
      }
      if (path === "/extract/ttps" && method === "POST") {
        let links = [];
        try {
          const raw = options.body ? JSON.parse(options.body) : {};
          links = Array.isArray(raw.links) ? raw.links : [];
        } catch {
          links = [];
        }
        const picked = links
          .map((link) => articles.find((a) => a.link === link))
          .filter(Boolean);
        const titles = picked.map((a) => a.title);

        // Article-derived seeds, then the matched curated pack(s) from the
        // shared registry (single source of truth with app.js).
        const behaviors = [];
        const email = [];
        const chat = [];
        const network = [];
        const seedSet = new Set();
        const human = [];
        const push = (arr, values) =>
          (values || []).forEach((v) => {
            const c = String(v || "").trim();
            if (c && !arr.some((x) => x.toLowerCase() === c.toLowerCase())) arr.push(c);
          });

        picked.forEach((a) => {
          (a.operator_terms || []).forEach((t) => seedSet.add(t));
          (a.itm_hits || []).forEach((h) =>
            (h.matched_aliases || []).forEach((t) => seedSet.add(t)),
          );
        });

        const itmIds = [];
        const texts = [];
        picked.forEach((a) => {
          (a.itm_hits || []).forEach((h) => itmIds.push(h.id));
          texts.push(a.title, a.source_name, a.source_id);
          (a.operator_terms || []).forEach((t) => texts.push(t));
        });
        const packsApi = window.InsiderIntelPacks;
        const selection = packsApi
          ? packsApi.selectPacks({ itmIds, texts })
          : { packs: [], matched: false };
        selection.packs.forEach((pack) => {
          pack.seeds.forEach((ttp) => {
            behaviors.push({ id: ttp.id, text: ttp.behavior });
            push(email, ttp.email);
            push(chat, ttp.chat);
            push(network, ttp.network);
            push(human, ttp.human);
            (ttp.seeds || []).forEach((t) => seedSet.add(t));
          });
        });
        // Report v2 mirror: per-technique sections built from the snapshot's
        // own ITM hits + case records (same evidence floor as the live API).
        const sections = new Map();
        picked.forEach((a) => {
          const record = a.case_record || null;
          const caseBullets = record
            ? [
                ...(record.methods || []),
                ...(record.exfil_channels || []).map((c) => `Exfil channel: ${c}`),
                ...(record.detection_trigger ? [`Detected via: ${record.detection_trigger}`] : []),
              ]
            : [];
          (a.itm_hits || []).forEach((h) => {
            const tid = String(h.id || "").toUpperCase();
            if (!tid) return;
            if (!sections.has(tid)) {
              sections.set(tid, { id: tid, title: h.title || tid, description: "", cases: [] });
            }
            const section = sections.get(tid);
            if (section.cases.some((c) => c.link === a.link)) return;
            const bullets = [...caseBullets];
            const aliases = (h.matched_aliases || []).slice(0, 6).join(", ");
            if (aliases) bullets.push(`Matched in text: ${aliases}`);
            section.cases.push({ title: a.title, link: a.link, bullets });
          });
        });
        const techniques = Array.from(sections.values());
        const summary = techniques.length
          ? `${picked.length} board case(s) show ${techniques.length} ITM technique(s): ` +
            `${techniques.map((s) => s.id).join(", ")}.`
          : "";

        const label = selection.packs.map((p) => p.label).join(" + ");
        return {
          mode: "seeds",
          article_count: picked.length,
          titles,
          summary,
          techniques,
          behaviors,
          email,
          chat,
          network,
          human,
          seeds: Array.from(seedSet),
          matched_if038: selection.matched && selection.packs.some((p) => p.id === "IF038"),
          detail: `Demo evidence pack · ${label || "board evidence"} (static snapshot — live API adds LLM enrichment)`,
        };
      }
      throw new Error(`Demo mode does not support ${method} ${path}`);
    },
  };
})();
