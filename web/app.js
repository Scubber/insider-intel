(() => {
  const demoModeInitial = Boolean(window.INSIDER_INTEL_DEMO);
  let demoMode = demoModeInitial;
  const apiBase = (window.INSIDER_INTEL_API_BASE || "http://127.0.0.1:8000").replace(
    /\/$/,
    "",
  );

  const UI_MIN_SCORE = 0.15;

  /** High-signal OSINT phrasing; keep aligned with shared/itm/aliases.py seeds. */
  const CLIENT_ALIAS_EXTRAS = {
    IF038: {
      title: "Undisclosed Concurrent Employment",
      theme: "infringement",
      aliases: [
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
    },
  };

  /** Vetted Hunt use cases — operator-facing chips under the search bar.
   * Chips with a useCase id filter the stream on the classified facet
   * (keep ids aligned with shared/taxonomy/use_cases.py); query-only chips
   * fall back to a text hunt. */
  // Descriptions mirror shared/taxonomy/use_cases.py — keep in sync.
  const HUNT_USE_CASES = [
    {
      label: "Overemployment",
      useCase: "overemployment",
      query: "overemployment moonlighting",
      description:
        "Undisclosed concurrent employment — secretly working multiple jobs (incl. moonlighting)",
    },
    {
      label: "Data exfiltration",
      useCase: "data-exfiltration",
      query: "data exfiltration trade secret",
      description: "Taking or leaking company data, files, or trade secrets",
    },
    {
      label: "Credential misuse",
      useCase: "credential-misuse",
      query: "shared credentials",
      description:
        "Sharing, borrowing, or abusing logins, badges, and privileged access",
    },
    {
      label: "Shadow IT",
      useCase: "shadow-it",
      query: "shadow it",
      description: "Unsanctioned apps, devices, or AI tools used for work",
    },
  ];

  const USE_CASE_LABELS = HUNT_USE_CASES.reduce((acc, item) => {
    if (item.useCase) acc[item.useCase] = item.label;
    return acc;
  }, {});

  const INSIDER_TYPE_LABELS = {
    malicious: "Malicious",
    negligent: "Negligent",
    unintentional: "Unintentional",
  };

  /** Curated IF038 TTP seeds — keep aligned with docs/ttps_overemployment.md */
  const IF038_TTP_SEEDS = [
    {
      id: "TTP-OE-01",
      behavior: "Undisclosed second full-time remote job (dual employment / overemployment).",
      email: [
        "personal-domain mail during work hours",
        "Job B recruiter/HR threads",
        "personal calendar invites for Job B standups",
      ],
      chat: [
        "second Slack/Teams identity",
        "J2 / OE / overemployed language",
        "status always Busy/BRB",
      ],
      network: [
        "concurrent SaaS sessions for different orgs",
        "personal VPN + corp VPN patterns",
        "after-hours bursty productivity tools",
      ],
      human: [
        "missing/false outside-employment or COI disclosure",
        "dual W-2 / multiple employers on tax or benefits",
        "LinkedIn current roles vs HRIS title mismatch",
      ],
      seeds: [
        "outside employment",
        "moonlighting",
        "J2",
        "overemployed",
        "second job",
        "dual employment",
        "conflict of interest disclosure",
      ],
    },
    {
      id: "TTP-OE-02",
      behavior: "Competitor / customer side work (trade-secret adjacent concurrent role).",
      email: [
        "competitor-domain threads",
        "side project share of internal decks",
        "personal Dropbox/Drive links in corp mail",
      ],
      chat: [
        "screenshots of internal tools",
        "my other company",
        "recruiting coworkers",
      ],
      network: [
        "large personal-cloud uploads",
        "USB/email exfil near resignation",
        "repos unused in day job",
      ],
      human: [
        "undisclosed advisory/contractor role",
        "COI form none",
        "resignation timed with competitor start",
      ],
      seeds: [
        "competitor",
        "side project",
        "advisory",
        "consulting agreement",
        "DTSA",
        "trade secret",
        "customer list",
      ],
    },
    {
      id: "TTP-OE-03",
      behavior: "Using Employer A time/tools for Employer B.",
      email: [
        "drafts to Job B from corp mailbox",
        "vague calendar blocks with no corp attendees",
      ],
      chat: [
        "Job B tickets pasted into corp chat",
        "second browser profile language",
      ],
      network: [
        "Job B IdP on corp device",
        "RDP/VDI to personal systems",
        "clipboard/file activity to personal cloud",
      ],
      human: [
        "timekeeping anomalies",
        "always in meetings without corp artifacts",
        "PIP for availability",
      ],
      seeds: ["personal laptop", "my other job", "client call"],
    },
    {
      id: "TTP-OE-04",
      behavior: "Identity split — personal stack for Job B, corp stack for Job A.",
      email: ["auto-forward corp to personal", "Job B never on corp systems"],
      chat: ["text me on my personal", "Signal/WhatsApp for work topics"],
      network: ["MDM gaps", "personal hotspot only", "corp VPN idle while claiming hours"],
      human: [
        "unreachable on corp mobile",
        "refuses MDM on personal devices used for work",
      ],
      seeds: ["personal phone", "text me", "Signal", "WhatsApp", "forward to Gmail"],
    },
    {
      id: "TTP-OE-05",
      behavior: "False or incomplete outside-employment / COI disclosure.",
      email: [
        "outside employment policy signature threads unanswered",
        "policy reminders ignored",
      ],
      chat: ["don't tell HR", "policy screenshot shares"],
      network: ["pair with HRIS — low network signal alone"],
      human: [
        "form answers vs LinkedIn/tax/benefits",
        "AP payments to employee LLC",
        "1099s",
      ],
      seeds: [
        "outside employment policy",
        "conflict of interest form",
        "disclosure form",
        "moonlighting policy",
      ],
    },
  ];

  const BOARD_STORAGE_KEY = "insider-intel.extractionBoard";
  const DISMISSED_STORAGE_KEY = "insider-intel.dismissed";
  const DISMISSED_CAP = 500;

  function techniqueAliases(tech) {
    const extra = CLIENT_ALIAS_EXTRAS[String(tech.id || "").toUpperCase()];
    const extras = (extra && extra.aliases) || [];
    const aliases = [...(tech.aliases || [])];
    const seen = new Set(aliases.map((a) => String(a).toLowerCase()));
    extras.forEach((a) => {
      const key = String(a).toLowerCase();
      if (!seen.has(key)) {
        seen.add(key);
        aliases.push(a);
      }
    });
    return aliases;
  }

  const THEME_ARTICLE = {
    motive: "AR1",
    means: "AR2",
    preparation: "AR3",
    infringement: "AR4",
    "anti-forensics": "AR5",
  };

  const MATRIX_THEMES = [
    { id: "motive", label: "Motive" },
    { id: "means", label: "Means" },
    { id: "preparation", label: "Preparation" },
    { id: "infringement", label: "Infringement" },
    { id: "anti-forensics", label: "Anti-Forensics" },
  ];

  const ITM_ID_RE = /^[A-Z]{2}\d{3}(?:\.\d+)?$/i;

  const els = {
    status: document.getElementById("status"),
    dataState: document.getElementById("data-state"),
    streamFilters: document.getElementById("stream-filters"),
    searchForm: document.getElementById("search-form"),
    sourceSelect: document.getElementById("source-select"),
    articleList: document.getElementById("article-list"),
    filterCrumbs: document.getElementById("filter-crumbs"),
    streamTitle: document.getElementById("stream-title"),
    streamCount: document.getElementById("stream-count"),
    refreshStream: document.getElementById("refresh-stream"),
    filterContext: document.getElementById("filter-context"),
    huntMap: document.getElementById("hunt-map"),
    huntMapList: document.getElementById("hunt-map-list"),
    huntMapEmpty: document.getElementById("hunt-map-empty"),
    matrixLatest: document.getElementById("matrix-latest"),
    mobileTabs: document.getElementById("mobile-tabs"),
    appWorkbench: document.getElementById("app-workbench"),
    q: document.getElementById("q"),
    clearSearch: document.getElementById("clear-search"),
    huntUsecases: document.getElementById("hunt-usecases"),
    panelEmpty: document.getElementById("panel-empty"),
    panelBody: document.getElementById("panel-body"),
    panelTitle: document.getElementById("panel-title"),
    panelMeta: document.getElementById("panel-meta"),
    panelLink: document.getElementById("panel-link"),
    operatorList: document.getElementById("operator-list"),
    itmList: document.getElementById("itm-list"),
    detectionList: document.getElementById("detection-list"),
    copyPlaintext: document.getElementById("copy-plaintext"),
    boardToggle: document.getElementById("board-toggle"),
    boardCount: document.getElementById("board-count"),
    boardList: document.getElementById("board-list"),
    boardEmpty: document.getElementById("board-empty"),
    boardExtract: document.getElementById("board-extract"),
    boardClear: document.getElementById("board-clear"),
    boardCopyBrief: document.getElementById("board-copy-brief"),
    boardShare: document.getElementById("board-share"),
    boardExport: document.getElementById("board-export"),
    boardImport: document.getElementById("board-import"),
    boardImportFile: document.getElementById("board-import-file"),
    boardBadge: document.getElementById("board-badge"),
    ttpReport: document.getElementById("ttp-report"),
    ttpReportMeta: document.getElementById("ttp-report-meta"),
    ttpBehaviorList: document.getElementById("ttp-behavior-list"),
    ttpEmailList: document.getElementById("ttp-email-list"),
    ttpChatList: document.getElementById("ttp-chat-list"),
    ttpNetworkList: document.getElementById("ttp-network-list"),
    ttpHumanList: document.getElementById("ttp-human-list"),
    ttpSeedList: document.getElementById("ttp-seed-list"),
    copyTtpReport: document.getElementById("copy-ttp-report"),
    copyTtpLlm: document.getElementById("copy-ttp-llm"),
    alignFilters: document.getElementById("align-filters"),
    channelFilters: document.getElementById("channel-filters"),
    insiderTypeFilters: document.getElementById("insider-type-filters"),
    socialManager: document.getElementById("social-manager"),
    socialSubscribed: document.getElementById("social-subscribed"),
    socialSubscribedEmpty: document.getElementById("social-subscribed-empty"),
    socialSuggested: document.getElementById("social-suggested"),
    socialAddForm: document.getElementById("social-add-form"),
    socialAddPlatform: document.getElementById("social-add-platform"),
    socialAddHandle: document.getElementById("social-add-handle"),
    refinePanel: document.getElementById("refine-panel"),
    refineState: document.getElementById("refine-state"),
    matrixQ: document.getElementById("matrix-q"),
    matrixModeTabs: document.getElementById("matrix-mode-tabs"),
    matrixColumns: document.getElementById("matrix-columns"),
    matrixControlList: document.getElementById("matrix-control-list"),
    articlePanel: document.getElementById("article-panel"),
    dossierPanel: document.getElementById("dossier-panel"),
    dossierBack: document.getElementById("dossier-back"),
    dossierTitle: document.getElementById("dossier-title"),
    dossierMeta: document.getElementById("dossier-meta"),
    dossierDesc: document.getElementById("dossier-desc"),
    dossierItmLink: document.getElementById("dossier-itm-link"),
    dossierTermList: document.getElementById("dossier-term-list"),
    dossierDetectionList: document.getElementById("dossier-detection-list"),
    dossierPreventionList: document.getElementById("dossier-prevention-list"),
    dossierArticleList: document.getElementById("dossier-article-list"),
    dossierCaseCount: document.getElementById("dossier-case-count"),
    dossierQueries: document.getElementById("dossier-queries"),
    ttpQueries: document.getElementById("ttp-queries"),
  };

  const state = {
    sourceId: "",
    theme: "",
    itmAlignment: "insider",
    channel: "all",
    useCase: "",
    insiderType: "all",
    articles: [],
    clusters: [],
    selectedLink: null,
    searchMode: false,
    lastHuntQuery: "",
    huntMappedIds: [],
    lastTotalIndexed: 0,
    itmCatalog: null,
    itmCatalogKey: "",
    matrixQuery: "",
    matrixMode: "techniques",
    selectedTechniqueId: null,
    selectedDetectionId: null,
    selectedPreventionId: null,
    linkedTechniques: [],
    expandedParents: new Set(),
    collapsedThemes: new Set(),
    extractionBoard: {},
    lastTtpReport: null,
    view: "stream",
    dossierTechniqueId: null,
    dataState: null,
    dismissed: new Set(),
    cursorIndex: -1,
  };

  const MOBILE_MQ = window.matchMedia("(max-width: 960px)");
  const WIDE_MQ = window.matchMedia("(min-width: 1200px)");
  const PANES = new Set(["articles", "matrix", "workbench"]);

  function isMobileLayout() {
    return MOBILE_MQ.matches;
  }

  function isWideLayout() {
    return WIDE_MQ.matches;
  }

  function setActivePane(pane) {
    const next = PANES.has(pane) ? pane : "articles";
    if (els.appWorkbench) {
      els.appWorkbench.dataset.pane = next;
    }
    if (els.mobileTabs) {
      els.mobileTabs.querySelectorAll(".mobile-tab").forEach((btn) => {
        btn.classList.toggle("active", btn.dataset.pane === next);
      });
    }
  }

  function syncPaneForViewport() {
    const current = els.appWorkbench?.dataset.pane;
    // Wide layout shows every pane; park the tab state on "articles" so
    // narrowing the window later never lands on a matrix-takeover view.
    if (isWideLayout()) {
      if (current !== "articles") setActivePane("articles");
      return;
    }
    if (!isMobileLayout()) return;
    if (!PANES.has(current)) setActivePane("articles");
  }

  /* Hash router — #/ (stream), #/technique/<ID> (dossier). GH Pages friendly. */
  let suppressRoute = false;

  function parseRoute() {
    const raw = location.hash || "";
    const path = raw.startsWith("#") ? raw.slice(1) : raw;
    if (path.startsWith("/technique/")) {
      const id = decodeURIComponent(path.slice("/technique/".length)).trim();
      if (id) return { view: "technique", id: id.toUpperCase() };
    }
    if (path.startsWith("/board/")) {
      const rest = path.slice("/board/".length);
      const slash = rest.indexOf("/");
      if (slash > 0) {
        return {
          view: "board",
          variant: rest.slice(0, slash),
          payload: rest.slice(slash + 1),
        };
      }
    }
    return { view: "stream" };
  }

  function navigate(path) {
    const target = `#${path}`;
    if (location.hash === target) return;
    suppressRoute = true;
    location.hash = target;
  }

  async function applyRoute(route) {
    if (route.view === "technique" && route.id) {
      await showDossier(route.id);
      return;
    }
    if (route.view === "board") {
      await importBoardFromRoute(route);
      return;
    }
    // Only reload the stream when leaving the dossier; hunt/matrix stream
    // states never change the hash, so a same-route event is a no-op.
    if (state.view === "dossier") {
      await showLatest();
    }
  }

  window.addEventListener("hashchange", () => {
    if (suppressRoute) {
      suppressRoute = false;
      return;
    }
    applyRoute(parseRoute()).catch((err) => setStatus(`Load failed: ${err.message}`));
  });

  function setView(view) {
    state.view = view;
    const isDossier = view === "dossier";
    if (els.articlePanel) els.articlePanel.hidden = isDossier;
    if (els.dossierPanel) els.dossierPanel.hidden = !isDossier;
    if (!isDossier) state.dossierTechniqueId = null;
  }

  const THEME_KEY = "insider-intel-theme";
  const THEMES = new Set(["cnn-lite", "midnight", "phosphor"]);
  const themeSelect = document.getElementById("theme-select");

  function applyTheme(name) {
    const theme = THEMES.has(name) ? name : "cnn-lite";
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem(THEME_KEY, theme);
    if (themeSelect) themeSelect.value = theme;
  }

  if (themeSelect) {
    applyTheme(localStorage.getItem(THEME_KEY) || "cnn-lite");
    themeSelect.addEventListener("change", () => applyTheme(themeSelect.value));
  }

  function setStatus(text) {
    els.status.textContent = text;
  }

  function renderDataState() {
    if (!els.dataState) return;
    const ds = state.dataState;
    if (!ds) {
      els.dataState.hidden = true;
      return;
    }
    const indexed = Number(ds.indexed || 0).toLocaleString();
    if (ds.mode === "demo") {
      const when = ds.generatedAt ? ` · ${formatDate(ds.generatedAt)}` : "";
      els.dataState.textContent = `Snapshot${when} · ${indexed} indexed`;
    } else {
      els.dataState.textContent = `Live · ${indexed} indexed`;
    }
    els.dataState.classList.toggle("data-state-demo", ds.mode === "demo");
    els.dataState.title =
      ds.mode === "demo"
        ? "Static demo snapshot — not live ingest"
        : "Connected to the live API";
    els.dataState.hidden = false;
  }

  function syncHuntUsecases() {
    if (!els.huntUsecases) return;
    const active = (state.lastHuntQuery || "").trim().toLowerCase();
    els.huntUsecases.querySelectorAll(".hunt-usecase").forEach((btn) => {
      const q = (btn.dataset.query || "").toLowerCase();
      const uc = btn.dataset.useCase || "";
      const isFacet = Boolean(uc) && uc === state.useCase;
      const isHunt = Boolean(active) && q === active;
      btn.classList.toggle("active", isFacet || isHunt);
    });
    const hint = document.getElementById("usecase-hint");
    if (hint) {
      const activeItem = HUNT_USE_CASES.find(
        (item) => item.useCase && item.useCase === state.useCase,
      );
      hint.textContent = activeItem
        ? `${activeItem.label}: ${activeItem.description}`
        : "Pick a hunt use case, then refine by insider type below.";
    }
  }

  function renderHuntUsecases() {
    if (!els.huntUsecases) return;
    els.huntUsecases.innerHTML = "";
    HUNT_USE_CASES.forEach((item) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "hunt-usecase";
      btn.dataset.query = item.query;
      if (item.useCase) btn.dataset.useCase = item.useCase;
      btn.textContent = item.label;
      btn.dataset.tip =
        item.description ||
        (item.useCase ? `Filter stream: ${item.label}` : `Hunt: ${item.query}`);
      btn.addEventListener("click", () => {
        if (item.useCase) {
          // Toggle the classified use-case facet on the stream.
          state.useCase = state.useCase === item.useCase ? "" : item.useCase;
          syncHuntUsecases();
          updateRefineSummary();
          reapplyActiveFilters().catch((err) => setStatus(`Load failed: ${err.message}`));
        } else {
          if (els.q) els.q.value = item.query;
          runSearch(item.query).catch((err) => setStatus(`Search failed: ${err.message}`));
        }
        if (isMobileLayout()) setActivePane("articles");
      });
      els.huntUsecases.appendChild(btn);
    });
    syncHuntUsecases();
  }

  async function api(path, params = {}, options = {}) {
    if (demoMode) {
      if (!window.InsiderIntelDemo) {
        throw new Error("Demo store not loaded");
      }
      return window.InsiderIntelDemo.request(path, params, options);
    }
    const url = new URL(`${apiBase}${path}`);
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== "") {
        url.searchParams.set(key, String(value));
      }
    });
    const timeoutMs = Number(options.timeoutMs) || 0;
    const { timeoutMs: _drop, ...fetchOpts } = options;
    let res;
    if (timeoutMs > 0) {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), timeoutMs);
      try {
        res = await fetch(url, { ...fetchOpts, signal: controller.signal });
      } catch (err) {
        if (err && err.name === "AbortError") {
          throw new Error(`timeout after ${timeoutMs}ms`);
        }
        throw err;
      } finally {
        clearTimeout(timer);
      }
    } else {
      res = await fetch(url, fetchOpts);
    }
    if (!res.ok) {
      throw new Error(`${res.status} ${res.statusText}`);
    }
    if (res.status === 204) return null;
    const text = await res.text();
    return text ? JSON.parse(text) : null;
  }

  function formatDate(value) {
    if (!value) return "unknown date";
    try {
      return new Date(value).toLocaleDateString(undefined, {
        year: "numeric",
        month: "short",
        day: "numeric",
      });
    } catch {
      return "unknown date";
    }
  }

  function itmUrl(hit) {
    const articleId = hit.article_id || THEME_ARTICLE[hit.theme] || "AR4";
    const sectionId = String(hit.id || "").split(".")[0];
    return `https://insiderthreatmatrix.org/articles/${articleId}/sections/${sectionId}`;
  }

  function detectionUrl(control) {
    return `https://insiderthreatmatrix.org/detections/${control.id}`;
  }

  function preventionUrl(control) {
    return `https://insiderthreatmatrix.org/preventions/${control.id}`;
  }

  function hasMatrixFilter() {
    return Boolean(
      state.selectedTechniqueId ||
        state.selectedDetectionId ||
        state.selectedPreventionId,
    );
  }

  function emptyMessage(override) {
    if (override) return override;
    if (hasMatrixFilter()) {
      return "No indexed articles for this matrix selection yet.";
    }
    if (state.searchMode && state.huntMappedIds.length) {
      return `Mapped to ${state.huntMappedIds.join(", ")} — no indexed stories yet for this Source/Channel. Try Filings, clear Source, or Refresh after ingest.`;
    }
    if (state.searchMode && els.q && els.q.value.trim()) {
      return `No ITM map for “${els.q.value.trim()}”. Add an alias in shared/itm/aliases.py if this is a real insider-risk phrase.`;
    }
    if (state.itmAlignment === "all") {
      if (state.sourceId || state.lastTotalIndexed > 0) {
        return "None found for this filter.";
      }
      return "No articles yet. Try Refresh after an ingest run.";
    }
    if (state.sourceId || state.lastTotalIndexed > 0) {
      return "No focused insider scenarios for this filter.";
    }
    return "No focused insider scenarios yet. Try Refresh after an ingest run.";
  }

  async function copyText(text, okMessage) {
    const value = String(text || "");
    if (!value) {
      setStatus(okMessage || "Nothing to copy");
      return;
    }

    // Prefer async clipboard API (HTTPS + user gesture).
    if (navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
      try {
        await navigator.clipboard.writeText(value);
        setStatus(okMessage);
        return;
      } catch {
        /* fall through — common on iOS / embedded browsers */
      }
    }

    // Synchronous fallback works more reliably on mobile Safari.
    const ta = document.createElement("textarea");
    ta.value = value;
    ta.setAttribute("readonly", "");
    ta.setAttribute("aria-hidden", "true");
    ta.style.cssText =
      "position:fixed;top:0;left:0;width:1px;height:1px;padding:0;border:0;opacity:0;";
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    ta.setSelectionRange(0, value.length);
    let ok = false;
    try {
      ok = document.execCommand("copy");
    } catch {
      ok = false;
    }
    document.body.removeChild(ta);
    setStatus(ok ? okMessage : "Clipboard blocked — long-press and copy from the report");
  }

  function composeOperatorTerms(article) {
    const fromApi = article.operator_terms || [];
    if (fromApi.length) return fromApi;

    const seen = new Set();
    const terms = [];
    const add = (value) => {
      const cleaned = String(value || "").trim();
      if (cleaned.length < 3 || ITM_ID_RE.test(cleaned)) return;
      const key = cleaned.toLowerCase();
      if (seen.has(key)) return;
      seen.add(key);
      terms.push(cleaned);
    };

    (article.itm_hits || []).forEach((hit) => {
      (hit.matched_aliases || []).forEach(add);
    });
    (article.keywords_hit || []).forEach(add);
    (article.cves || []).forEach(add);
    (article.domains || []).forEach(add);
    return terms;
  }

  function selectedArticle() {
    return state.articles.find((a) => a.link === state.selectedLink) || null;
  }

  function loadDismissed() {
    try {
      const raw = localStorage.getItem(DISMISSED_STORAGE_KEY);
      const arr = raw ? JSON.parse(raw) : [];
      state.dismissed = new Set(Array.isArray(arr) ? arr.map(String) : []);
    } catch {
      state.dismissed = new Set();
    }
  }

  function saveDismissed() {
    const arr = [...state.dismissed].slice(-DISMISSED_CAP);
    state.dismissed = new Set(arr);
    localStorage.setItem(DISMISSED_STORAGE_KEY, JSON.stringify(arr));
  }

  function clusterKey(cluster) {
    return String(
      (cluster && (cluster.story_key || (cluster.primary && cluster.primary.link))) || "",
    );
  }

  function toggleDismissed(cluster, rowEl) {
    const key = clusterKey(cluster);
    if (!key) return;
    const dismissed = !state.dismissed.has(key);
    if (dismissed) state.dismissed.add(key);
    else state.dismissed.delete(key);
    saveDismissed();
    const row =
      rowEl ||
      document.querySelector(`.article-row[data-story-key="${CSS.escape(key)}"]`);
    if (row) row.classList.toggle("dismissed", dismissed);
    setStatus(dismissed ? "Dismissed — d to restore" : "Restored");
  }

  function loadExtractionBoard() {
    try {
      const raw = localStorage.getItem(BOARD_STORAGE_KEY);
      const parsed = raw ? JSON.parse(raw) : {};
      state.extractionBoard =
        parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
    } catch {
      state.extractionBoard = {};
    }
  }

  function saveExtractionBoard() {
    localStorage.setItem(BOARD_STORAGE_KEY, JSON.stringify(state.extractionBoard));
  }

  function boardEntries() {
    return Object.values(state.extractionBoard);
  }

  function articleOnBoard(link) {
    return Boolean(link && state.extractionBoard[link]);
  }

  function boardItemFromArticle(article) {
    const itmIds = (article.itm_hits || [])
      .map((h) => String(h.id || "").toUpperCase())
      .filter(Boolean);
    return {
      link: article.link,
      title: article.title || article.link,
      source_id: article.source_id || "",
      source_name: article.source_name || article.source_id || "",
      channel: article.channel || "news",
      itm_ids: itmIds,
      operator_terms: composeOperatorTerms(article),
      matched_aliases: (article.itm_hits || []).flatMap((h) => h.matched_aliases || []),
    };
  }

  function addToBoard(article, options = {}) {
    if (!article || !article.link) return;
    state.extractionBoard[article.link] = boardItemFromArticle(article);
    saveExtractionBoard();
    renderExtractionBoard();
    syncBoardToggle();
    syncStreamBoardButtons();
    const n = boardEntries().length;
    setStatus(`On board · ${n} — Extract TTPs when ready`);
    if (options.focusWorkbench && isMobileLayout()) {
      setActivePane("workbench");
    }
  }

  function removeFromBoard(link) {
    if (!link || !state.extractionBoard[link]) return;
    delete state.extractionBoard[link];
    saveExtractionBoard();
    renderExtractionBoard();
    syncBoardToggle();
    syncStreamBoardButtons();
    setStatus(`On board · ${boardEntries().length} — Extract TTPs when ready`);
  }

  function clearBoard() {
    state.extractionBoard = {};
    saveExtractionBoard();
    state.lastTtpReport = null;
    if (els.ttpReport) els.ttpReport.hidden = true;
    renderExtractionBoard();
    syncBoardToggle();
    syncStreamBoardButtons();
    setStatus("Extraction board cleared");
  }

  function syncBoardToggle() {
    if (!els.boardToggle) return;
    const on = articleOnBoard(state.selectedLink);
    els.boardToggle.textContent = on ? "Remove from board" : "Add to board";
    els.boardToggle.disabled = !state.selectedLink;
    els.boardToggle.classList.toggle("copy-btn-primary", !on && Boolean(state.selectedLink));
  }

  function syncStreamBoardButtons() {
    document.querySelectorAll(".article-board-btn[data-link]").forEach((btn) => {
      const link = btn.dataset.link || "";
      const on = articleOnBoard(link);
      btn.classList.toggle("on-board", on);
      btn.textContent = on ? "✓" : "+";
      btn.title = on ? "Remove from board" : "Add to extraction board";
      btn.setAttribute("aria-label", btn.title);
    });
  }

  function placeholderBoardEntry(link) {
    let host = "";
    try {
      host = new URL(link).hostname;
    } catch {
      host = String(link);
    }
    return {
      link,
      title: `${host} (imported)`,
      source_id: "",
      source_name: host,
      channel: "news",
      itm_ids: [],
      operator_terms: [],
      matched_aliases: [],
    };
  }

  async function hydrateBoardLinks(links) {
    const byLink = new Map();
    let missing = [];
    const CHUNK = 40; // /articles/by-links request bound
    try {
      for (let i = 0; i < links.length; i += CHUNK) {
        const chunk = links.slice(i, i + CHUNK);
        const data = await api(
          "/articles/by-links",
          {},
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ links: chunk }),
            timeoutMs: 10000,
          },
        );
        ((data && data.results) || []).forEach((hit) => byLink.set(hit.link, hit));
        missing = missing.concat((data && data.missing) || []);
      }
    } catch {
      // Older API or offline: placeholders keep the board usable — Extract
      // still works because /extract/ttps resolves raw links itself.
      missing = links.filter((link) => !byLink.has(link));
    }
    const entries = links.map((link) => {
      const hit = byLink.get(link);
      return hit ? boardItemFromArticle(hit) : placeholderBoardEntry(link);
    });
    return { entries, missing };
  }

  async function importBoardFromRoute(route) {
    try {
      if (!window.InsiderIntelBoardShare) throw new Error("share codec not loaded");
      const links = await window.InsiderIntelBoardShare.decodeBoard(
        route.payload,
        route.variant,
      );
      if (!links.length) throw new Error("empty board payload");
      setStatus(`Importing ${links.length} board item(s)…`);
      const fresh = links.filter((link) => !state.extractionBoard[link]);
      const { entries, missing } = await hydrateBoardLinks(fresh);
      entries.forEach((entry) => {
        state.extractionBoard[entry.link] = entry;
      });
      saveExtractionBoard();
      renderExtractionBoard();
      syncBoardToggle();
      syncStreamBoardButtons();
      setActivePane("workbench");
      const skipped = links.length - fresh.length;
      const parts = [`Imported ${fresh.length} board item(s)`];
      if (skipped) parts.push(`${skipped} already on board`);
      if (missing.length) parts.push(`${missing.length} not in this index`);
      setStatus(parts.join(" · "));
    } catch (err) {
      setStatus(`Board import failed: ${err.message}`);
    } finally {
      // Strip the payload so refresh / back don't re-import.
      history.replaceState(null, "", `${location.pathname}${location.search}#/`);
    }
  }

  async function shareBoardLink() {
    const entries = boardEntries();
    if (!entries.length) {
      setStatus("Add articles to the extraction board first");
      return;
    }
    if (!window.InsiderIntelBoardShare) {
      setStatus("Share codec not loaded");
      return;
    }
    try {
      const { variant, payload } = await window.InsiderIntelBoardShare.encodeBoard(
        entries.map((e) => e.link),
      );
      const url = `${location.origin}${location.pathname}#/board/${variant}/${payload}`;
      const warn =
        url.length > 1800 ? " — long link; use Export for chat apps" : "";
      await copyText(url, `Copied board link (${entries.length} item(s))${warn}`);
    } catch (err) {
      setStatus(`Share failed: ${err.message}`);
    }
  }

  function exportBoardFile() {
    const entries = boardEntries();
    if (!entries.length) {
      setStatus("Add articles to the extraction board first");
      return;
    }
    const blob = new Blob(
      [JSON.stringify({ v: 1, board: state.extractionBoard }, null, 2)],
      { type: "application/json" },
    );
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "insider-intel-board.json";
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    setStatus(`Exported ${entries.length} board item(s)`);
  }

  function importBoardData(data) {
    const incoming =
      data &&
      data.v === 1 &&
      data.board &&
      typeof data.board === "object" &&
      !Array.isArray(data.board)
        ? Object.values(data.board)
        : null;
    if (!incoming) throw new Error("unrecognized board file");
    let added = 0;
    incoming.forEach((item) => {
      if (!item || typeof item !== "object" || !item.link) return;
      if (!state.extractionBoard[item.link]) added += 1;
      state.extractionBoard[item.link] = {
        ...placeholderBoardEntry(item.link),
        ...item,
      };
    });
    saveExtractionBoard();
    renderExtractionBoard();
    syncBoardToggle();
    syncStreamBoardButtons();
    setStatus(`Imported ${added} new board item(s) · ${boardEntries().length} total`);
  }

  function renderExtractionBoard() {
    const entries = boardEntries();
    const n = entries.length;
    if (els.boardCount) els.boardCount.textContent = `(${n})`;
    if (els.boardExtract) els.boardExtract.disabled = n === 0;
    if (els.boardClear) els.boardClear.disabled = n === 0;
    if (els.boardCopyBrief) els.boardCopyBrief.disabled = n === 0;
    if (els.boardShare) els.boardShare.disabled = n === 0;
    if (els.boardExport) els.boardExport.disabled = n === 0;
    if (els.boardBadge) {
      if (n > 0) {
        els.boardBadge.hidden = false;
        els.boardBadge.textContent = String(n);
      } else {
        els.boardBadge.hidden = true;
      }
    }
    if (els.boardEmpty) els.boardEmpty.hidden = n > 0;
    if (!els.boardList) return;
    els.boardList.hidden = n === 0;
    els.boardList.innerHTML = "";
    entries.forEach((item) => {
      const li = document.createElement("li");
      li.className = "board-item";
      const main = document.createElement("div");
      main.className = "board-item-main";
      const title = document.createElement("p");
      title.className = "board-item-title";
      title.textContent = item.title;
      const meta = document.createElement("p");
      meta.className = "board-item-meta";
      meta.textContent = `${item.source_name || item.source_id || "source"} · ${item.channel || "news"}`;
      main.append(title, meta);
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "board-item-remove";
      remove.textContent = "Remove";
      remove.addEventListener("click", () => removeFromBoard(item.link));
      li.append(main, remove);
      els.boardList.appendChild(li);
    });
  }

  function if038AliasHints() {
    return (CLIENT_ALIAS_EXTRAS.IF038.aliases || []).map((a) => String(a).toLowerCase());
  }

  function textHasIf038Alias(text) {
    const hay = String(text || "").toLowerCase();
    if (!hay) return false;
    return if038AliasHints().some((alias) => alias && hay.includes(alias));
  }

  function boardMatchedIf038(entries) {
    if ((state.huntMappedIds || []).some((id) => String(id).toUpperCase() === "IF038")) {
      return true;
    }
    if (textHasIf038Alias(state.lastHuntQuery)) return true;

    return (entries || []).some((item) => {
      if ((item.itm_ids || []).some((id) => String(id).toUpperCase() === "IF038")) {
        return true;
      }
      const blobs = [item.title, item.source_name, item.source_id]
        .concat(item.matched_aliases || [])
        .concat(item.operator_terms || []);
      return blobs.some((blob) => textHasIf038Alias(blob));
    });
  }

  function uniqPush(list, seen, value) {
    const cleaned = String(value || "").trim();
    if (!cleaned) return;
    const key = cleaned.toLowerCase();
    if (seen.has(key)) return;
    seen.add(key);
    list.push(cleaned);
  }

  function buildTtpReport(entries) {
    const behaviors = [];
    const email = [];
    const chat = [];
    const network = [];
    const human = [];
    const seeds = [];
    const seen = {
      email: new Set(),
      chat: new Set(),
      network: new Set(),
      human: new Set(),
      seeds: new Set(),
    };

    entries.forEach((item) => {
      (item.operator_terms || []).forEach((t) => uniqPush(seeds, seen.seeds, t));
      (item.matched_aliases || []).forEach((t) => uniqPush(seeds, seen.seeds, t));
    });

    // MVP: always attach curated IF038 TTP pack so Extract never yields an empty
    // hunt report. Matching on board/hunt only changes the meta label.
    const matchedIf038 = boardMatchedIf038(entries);
    IF038_TTP_SEEDS.forEach((ttp) => {
      behaviors.push({ id: ttp.id, text: ttp.behavior });
      ttp.email.forEach((t) => uniqPush(email, seen.email, t));
      ttp.chat.forEach((t) => uniqPush(chat, seen.chat, t));
      ttp.network.forEach((t) => uniqPush(network, seen.network, t));
      ttp.human.forEach((t) => uniqPush(human, seen.human, t));
      ttp.seeds.forEach((t) => uniqPush(seeds, seen.seeds, t));
    });

    return {
      articleCount: entries.length,
      titles: entries.map((e) => e.title),
      behaviors,
      email,
      chat,
      network,
      human,
      seeds,
      usedIf038Seeds: true,
      matchedIf038,
      mode: "seeds",
      detail: matchedIf038
        ? "Seed pack · IF038 matched"
        : "Seed pack · IF038 overemployment TTP pack",
    };
  }

  function fillPlainList(listEl, items, asTtpBehavior = false) {
    if (!listEl) return;
    listEl.innerHTML = "";
    (items || []).forEach((item) => {
      const li = document.createElement("li");
      if (asTtpBehavior && item && typeof item === "object") {
        const id = document.createElement("span");
        id.className = "ttp-id";
        id.textContent = item.id || "";
        li.appendChild(id);
        li.appendChild(document.createTextNode(` ${item.text || ""}`));
      } else {
        li.textContent = String(item);
      }
      listEl.appendChild(li);
    });
    if (!(items || []).length) {
      const li = document.createElement("li");
      li.textContent = "None yet — add articles to the board and Extract TTPs.";
      listEl.appendChild(li);
    }
  }

  function renderTtpReport(report) {
    state.lastTtpReport = report;
    if (!els.ttpReport) return;
    els.ttpReport.hidden = false;
    if (els.ttpReportMeta) {
      const mode = report.mode || (report.usedIf038Seeds ? "seeds" : "seeds");
      const modeLabel =
        mode === "llm"
          ? `LLM · ${report.articleCount} source(s)`
          : report.detail || `Seed pack · ${report.articleCount} article(s)`;
      els.ttpReportMeta.textContent = modeLabel;
    }
    fillPlainList(els.ttpBehaviorList, report.behaviors, true);
    fillCopyableChips(els.ttpEmailList, report.email, true);
    fillCopyableChips(els.ttpChatList, report.chat, true);
    fillCopyableChips(els.ttpNetworkList, report.network, true);
    fillCopyableChips(els.ttpHumanList, report.human, true);
    fillCopyableChips(els.ttpSeedList, report.seeds, true);
    renderQueryBlocks(els.ttpQueries, huntQueriesForReport(report));
    if (isMobileLayout()) setActivePane("workbench");
    try {
      els.ttpReport.scrollIntoView({ behavior: "smooth", block: "nearest" });
    } catch {
      /* ignore */
    }
  }

  function huntQueriesForReport(report) {
    if (!report) return [];
    return buildHuntQueries({
      terms: report.seeds || [],
      emailCues: report.email || [],
      chatCues: report.chat || [],
      networkCues: report.network || [],
    });
  }

  function ttpReportPlaintext(report) {
    if (!report) return "";
    const queries = huntQueriesForReport(report);
    const queryLines = queries.length
      ? [
          "",
          "Run it:",
          ...queries.flatMap((q) => [``, `## ${q.stack} (${q.lang})`, q.query]),
        ]
      : [];
    const lines = [
      "insider-intel hunt report (extraction board)",
      `Mode: ${report.mode || "seeds"}`,
      `Articles (${report.articleCount}):`,
      ...report.titles.map((t) => `- ${t}`),
      "",
      "Behaviors:",
      ...(report.behaviors.length
        ? report.behaviors.map((b) => `- ${b.id}: ${b.text}`)
        : ["- (none)"]),
      "",
      "Email:",
      ...report.email.map((t) => `- ${t}`),
      "",
      "Chat:",
      ...report.chat.map((t) => `- ${t}`),
      "",
      "Network:",
      ...report.network.map((t) => `- ${t}`),
      "",
      "Human / HR:",
      ...report.human.map((t) => `- ${t}`),
      "",
      "Paste / search seeds:",
      ...report.seeds.map((t) => `- ${t}`),
      ...queryLines,
    ];
    return lines.join("\n");
  }

  function ttpReportLlmQuery(report) {
    if (!report) return "";
    const behaviorLines = (report.behaviors || []).length
      ? report.behaviors.map((b) => `- ${b.id}: ${b.text}`)
      : ["- (none)"];
    const list = (items) =>
      (items || []).length ? items.map((t) => `- ${t}`) : ["- (none)"];
    return [
      "You are helping an insider-risk investigator turn an OSINT hunt report into",
      "actionable internal searches. Do NOT invent case facts. Prefer short, realistic",
      "query strings the analyst can paste into each stack.",
      "",
      "Produce four sections with concrete searches / review steps:",
      "1) Email / eDiscovery (Exchange, Gmail, Purview, similar)",
      "2) Chat / collab (Teams, Slack, Discord, similar)",
      "3) Network / identity / SaaS (SIEM, IdP, VPN, MDM, cloud apps)",
      "4) Human / HR / legal (HRIS, COI/outside-employment forms, LinkedIn vs role,",
      "   manager interview prompts — not SIEM-only)",
      "",
      "For each section return:",
      "- Paste-ready search strings (one per line)",
      "- 1–3 review steps if a query alone is not enough",
      "Avoid bare taxonomy IDs unless useful as keywords.",
      "",
      "=== Hunt report context ===",
      `Mode: ${report.mode || "seeds"}`,
      `Source articles (${report.articleCount || 0}):`,
      ...(report.titles || []).map((t) => `- ${t}`),
      "",
      "Behaviors:",
      ...behaviorLines,
      "",
      "Email cues:",
      ...list(report.email),
      "",
      "Chat cues:",
      ...list(report.chat),
      "",
      "Network cues:",
      ...list(report.network),
      "",
      "Human / HR cues:",
      ...list(report.human),
      "",
      "Seeds:",
      ...list(report.seeds),
      "",
      "=== End context ===",
      "Return only the four sections above with searches and steps.",
    ].join("\n");
  }

  function normalizeExtractResponse(data, fallbackEntries) {
    if (!data || typeof data !== "object") {
      return buildTtpReport(fallbackEntries);
    }
    const behaviors = (data.behaviors || []).map((b) =>
      b && typeof b === "object"
        ? { id: b.id || "TTP", text: b.text || b.behavior || "" }
        : { id: "TTP", text: String(b) },
    );
    return {
      mode: data.mode || "seeds",
      articleCount: data.article_count != null ? data.article_count : fallbackEntries.length,
      titles: data.titles || fallbackEntries.map((e) => e.title),
      behaviors,
      email: data.email || [],
      chat: data.chat || [],
      network: data.network || [],
      human: data.human || [],
      seeds: data.seeds || [],
      matchedIf038: Boolean(data.matched_if038),
      detail: data.detail || "",
      usedIf038Seeds: true,
    };
  }

  function agentBriefPlaintext(entries) {
    const lines = [
      "insider-intel extraction board — agent brief",
      "",
      "Use CourtListener MCP (https://mcp.courtlistener.com) to open dockets/opinions",
      "for filings below. Return JSON with keys:",
      'behaviors[{id,text}], email[], chat[], network[], human[], seeds[].',
      "Ground cues in the documents — do not invent case facts.",
      "",
      `Board articles (${entries.length}):`,
    ];
    entries.forEach((item, i) => {
      lines.push("");
      lines.push(`${i + 1}. ${item.title}`);
      lines.push(`   link: ${item.link}`);
      lines.push(`   source: ${item.source_name || item.source_id || ""} · ${item.channel || ""}`);
      if ((item.operator_terms || []).length) {
        lines.push(`   operator_terms: ${(item.operator_terms || []).slice(0, 12).join("; ")}`);
      }
      if ((item.itm_ids || []).length) {
        lines.push(`   itm: ${(item.itm_ids || []).join(", ")}`);
      }
    });
    return lines.join("\n");
  }

  async function runBoardExtract() {
    const entries = boardEntries();
    if (!entries.length) {
      setStatus("Add articles to the extraction board first");
      return;
    }
    // Mobile: show Workbench immediately so Extracting… / report are visible.
    setActivePane("workbench");
    const extractBtn = els.boardExtract;
    const prevLabel = extractBtn ? extractBtn.textContent : "";
    if (extractBtn) {
      extractBtn.disabled = true;
      extractBtn.textContent = "Extracting…";
    }
    setStatus("Extracting TTPs…");
    try {
      let report;
      try {
        const data = await api(
          "/extract/ttps",
          {},
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ links: entries.map((e) => e.link) }),
            timeoutMs: 10000,
          },
        );
        report = normalizeExtractResponse(data, entries);
      } catch (err) {
        console.warn("extract API failed, using local seed pack", err);
        report = buildTtpReport(entries);
        report.mode = "seeds";
        report.detail = "Extract offline · seed pack";
        setStatus(`Extract offline · seed pack`);
      }
      renderTtpReport(report);
      if (report.mode === "llm") {
        setStatus(`Extracted hunt report · LLM · ${entries.length} article(s)`);
      } else if (!String(els.status?.textContent || "").startsWith("Extract offline")) {
        setStatus(
          `Extracted hunt report · seed pack · ${entries.length} article(s)` +
            (report.detail ? ` · ${report.detail}` : ""),
        );
      }
    } catch (err) {
      console.error(err);
      setStatus(`Extract failed: ${err && err.message ? err.message : err}`);
    } finally {
      if (extractBtn) {
        extractBtn.textContent = prevLabel || "Extract TTPs";
        extractBtn.disabled = boardEntries().length === 0;
      }
    }
  }

  function clustersFromResponse(data) {
    if (data && Array.isArray(data.clusters) && data.clusters.length) {
      return data.clusters;
    }
    const results = (data && data.results) || (Array.isArray(data) ? data : []);
    return results.map((article) => ({
      story_key: article.story_key || article.link,
      channel: article.channel || "news",
      primary: article,
      siblings: [],
      member_count: 1,
    }));
  }

  function flattenClusterMembers(clusters) {
    const out = [];
    clusters.forEach((cluster) => {
      if (cluster.primary) out.push(cluster.primary);
      (cluster.siblings || []).forEach((sib) => out.push(sib));
    });
    return out;
  }

  function fillCopyableChips(listEl, items, signal = false) {
    if (!listEl) return;
    listEl.innerHTML = "";
    (items || []).forEach((item) => {
      const li = document.createElement("li");
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = signal ? "chip signal" : "chip";
      btn.textContent = item;
      btn.title = `Copy “${item}”`;
      btn.addEventListener("click", () => {
        copyText(item, `Copied “${item}”`);
      });
      li.appendChild(btn);
      listEl.appendChild(li);
    });
    if (!(items || []).length) {
      const li = document.createElement("li");
      li.className = "chip";
      li.textContent = "—";
      listEl.appendChild(li);
    }
  }

  function buildHuntQueries(input) {
    if (!window.InsiderIntelTemplates) return [];
    try {
      return window.InsiderIntelTemplates.buildQueries(input) || [];
    } catch {
      return [];
    }
  }

  function renderQueryBlocks(container, queries) {
    if (!container) return;
    container.innerHTML = "";
    if (!(queries || []).length) {
      const p = document.createElement("p");
      p.className = "kw-hint";
      p.textContent = "No hunt terms to build queries from yet.";
      container.appendChild(p);
      return;
    }
    queries.forEach((q) => {
      const details = document.createElement("details");
      details.className = "query-stack";
      const summary = document.createElement("summary");
      summary.className = "query-stack-summary";
      const label = document.createElement("span");
      label.textContent = q.label;
      const lang = document.createElement("span");
      lang.className = "query-stack-lang";
      lang.textContent = q.lang;
      summary.append(label, lang);
      const pre = document.createElement("pre");
      pre.className = "query-block";
      pre.textContent = q.query;
      const actions = document.createElement("p");
      actions.className = "panel-actions query-stack-actions";
      const copy = document.createElement("button");
      copy.type = "button";
      copy.className = "copy-btn";
      copy.textContent = "Copy query";
      copy.addEventListener("click", () => {
        copyText(q.query, `Copied ${q.stack} query`);
      });
      actions.appendChild(copy);
      details.append(summary, pre, actions);
      container.appendChild(details);
    });
  }

  function fillItmChips(listEl, hits) {
    listEl.innerHTML = "";
    (hits || []).forEach((hit) => {
      const li = document.createElement("li");
      li.className = "itm-chip-pair";
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "chip signal itm-chip";
      btn.title = `${hit.id} · open dossier`;
      btn.textContent = `${hit.id} · ${hit.title}`;
      btn.addEventListener("click", () => {
        selectTechnique(hit.id).catch((err) =>
          setStatus(`Technique load failed: ${err.message}`),
        );
      });
      const ext = document.createElement("a");
      ext.className = "chip itm-chip-ext";
      ext.href = itmUrl(hit);
      ext.target = "_blank";
      ext.rel = "noopener";
      ext.title = "Open in Insider Threat Matrix™";
      ext.textContent = "↗";
      li.append(btn, ext);
      listEl.appendChild(li);
    });
    if (!(hits || []).length) {
      const li = document.createElement("li");
      li.className = "chip";
      li.textContent = "No ITM matches";
      listEl.appendChild(li);
    }
  }

  function fillControlChips(listEl, controls, kind) {
    listEl.innerHTML = "";
    const urlFn = kind === "prevention" ? preventionUrl : detectionUrl;
    const emptyLabel =
      kind === "prevention" ? "No linked preventions" : "No linked detections";
    (controls || []).forEach((control) => {
      const li = document.createElement("li");
      const a = document.createElement("a");
      a.className = "chip signal itm-chip";
      a.href = urlFn(control);
      a.target = "_blank";
      a.rel = "noopener";
      a.title = `Copy ${control.id}`;
      a.textContent = `${control.id} · ${control.title}`;
      li.appendChild(a);
      listEl.appendChild(li);
    });
    if (!(controls || []).length) {
      const li = document.createElement("li");
      li.className = "chip";
      li.textContent = emptyLabel;
      listEl.appendChild(li);
    }
  }

  function handoffLines(article) {
    const lines = [];
    (article.related_detections || []).forEach((c) => {
      lines.push(`${c.id} ${c.title}`);
    });
    (article.related_preventions || []).forEach((c) => {
      lines.push(`${c.id} ${c.title}`);
    });
    return lines;
  }

  function plaintextExport(article) {
    const terms = composeOperatorTerms(article);
    const handoff = handoffLines(article);
    if (!handoff.length) return terms.join("\n");
    return [...terms, "", "# SIEM / control handoff", ...handoff].join("\n");
  }

  function jsonExport(article) {
    return JSON.stringify(
      {
        title: article.title,
        link: article.link,
        source: article.source_name,
        published: article.published,
        itm_hits: article.itm_hits || [],
        operator_terms: composeOperatorTerms(article),
        related_detections: article.related_detections || [],
        related_preventions: article.related_preventions || [],
        cves: article.cves || [],
        domains: article.domains || [],
        keywords_hit: article.keywords_hit || [],
      },
      null,
      2,
    );
  }

  function llmExport(article) {
    const terms = composeOperatorTerms(article);
    const itmIds = (article.itm_hits || []).map((h) => `${h.id} (${h.title})`);
    const detections = (article.related_detections || []).map(
      (c) => `${c.id} (${c.title})`,
    );
    const preventions = (article.related_preventions || []).map(
      (c) => `${c.id} (${c.title})`,
    );
    return [
      "Expand the following insider-threat OSINT article into searchable keywords",
      "for Microsoft Teams chats, email / eDiscovery, and a generic SIEM.",
      "Prefer short, realistic search strings. Avoid bare taxonomy IDs unless useful.",
      "Also suggest how an analyst might use the linked ITM detections/preventions.",
      "",
      `Title: ${article.title}`,
      `Link: ${article.link}`,
      `Source: ${article.source_name}`,
      `Summary: ${article.summary || "(none)"}`,
      "",
      `ITM techniques: ${itmIds.length ? itmIds.join("; ") : "(none)"}`,
      `Related detections: ${detections.length ? detections.join("; ") : "(none)"}`,
      `Related preventions: ${preventions.length ? preventions.join("; ") : "(none)"}`,
      "Current operator terms:",
      ...(terms.length ? terms.map((t) => `- ${t}`) : ["- (none)"]),
      "",
      "Return a deduped plain list of additional or refined search terms only.",
    ].join("\n");
  }

  function selectedTechnique() {
    const techniques = (state.itmCatalog && state.itmCatalog.techniques) || [];
    return techniques.find((t) => t.id === state.selectedTechniqueId) || null;
  }

  function techniqueMatchesQuery(tech, query) {
    if (!query) return true;
    const q = query.toLowerCase();
    const aliases = techniqueAliases(tech).map((a) => String(a).toLowerCase());
    return (
      String(tech.id || "").toLowerCase().includes(q) ||
      String(tech.title || "").toLowerCase().includes(q) ||
      aliases.some(
        (a) => a.includes(q) || (q.length >= 3 && a.length >= 3 && q.includes(a)),
      )
    );
  }

  function techniqueTitleWithCount(tech) {
    const count =
      typeof tech.article_count === "number" ? tech.article_count : 0;
    return `${tech.title} [${count}]`;
  }

  function techniqueHasCoverage(tech) {
    return (typeof tech.article_count === "number" ? tech.article_count : 0) > 0;
  }

  function childrenOf(parentId) {
    const techniques = (state.itmCatalog && state.itmCatalog.techniques) || [];
    return techniques.filter((t) => t.parent_id === parentId);
  }

  function catalogScopeKey() {
    return `${state.sourceId || ""}|${state.channel || "all"}`;
  }

  async function ensureItmCatalog(force = false) {
    const key = catalogScopeKey();
    if (!force && state.itmCatalog && state.itmCatalogKey === key) {
      return state.itmCatalog;
    }
    try {
      state.itmCatalog = await api("/itm", {
        source_id: state.sourceId || undefined,
        channel: state.channel && state.channel !== "all" ? state.channel : undefined,
      });
      state.itmCatalogKey = key;
    } catch (err) {
      // Keep prior catalog if any; Hunt can still map via CLIENT_ALIAS_EXTRAS.
      if (!state.itmCatalog) {
        state.itmCatalog = {
          techniques: Object.entries(CLIENT_ALIAS_EXTRAS).map(([id, meta]) => ({
            id,
            title: meta.title,
            theme: meta.theme,
            aliases: meta.aliases,
            article_count: 0,
            parent_id: null,
            detections: [],
            preventions: [],
          })),
          detections: [],
          preventions: [],
          articles: [],
        };
      }
      setStatus(`ITM catalog slow/unavailable (${err.message}) — using local alias map`);
    }
    return state.itmCatalog;
  }

  function mergeArticleLists(...lists) {
    const byLink = new Map();
    lists.flat().forEach((item) => {
      if (!item) return;
      const link = item.link || item.id;
      if (!link) return;
      if (!byLink.has(link)) byLink.set(link, item);
    });
    return Array.from(byLink.values());
  }

  async function fetchArticlesForTechniqueIds(techniqueIds, { limit = 50 } = {}) {
    const ids = [...new Set((techniqueIds || []).filter(Boolean))];
    if (!ids.length) {
      return { results: [], clusters: [], total_indexed: state.lastTotalIndexed, count: 0 };
    }
    const responses = await Promise.all(
      ids.map((itm_id) =>
        api("/articles", {
          limit,
          min_score: 0,
          itm_alignment: "all",
          channel: channelParam(),
          use_case: useCaseParam(),
          insider_type: insiderTypeParam(),
          source_id: state.sourceId || undefined,
          itm_id,
          topic_match: true,
          group: false,
        }),
      ),
    );
    const results = mergeArticleLists(...responses.map((r) => r.results || []));
    const total_indexed =
      (responses.find((r) => r.total_indexed) || {}).total_indexed ||
      state.lastTotalIndexed;
    return { results, clusters: [], total_indexed, count: results.length };
  }

  function linkedTechniqueIdsForControl(kind, controlId) {
    const techniques = (state.itmCatalog && state.itmCatalog.techniques) || [];
    const needle = String(controlId || "").toUpperCase();
    return techniques
      .filter((t) => {
        const list = kind === "prevention" ? t.preventions || [] : t.detections || [];
        return list.some((c) => String(c.id).toUpperCase() === needle);
      })
      .map((t) => t.id);
  }

  function updateFilterContext(label) {
    if (!els.filterContext) return;
    if (!hasMatrixFilter() || !label) {
      els.filterContext.hidden = true;
      els.filterContext.textContent = "";
      return;
    }
    els.filterContext.hidden = false;
    els.filterContext.textContent = label;
  }

  function clearHuntMap() {
    if (els.huntMap) els.huntMap.hidden = true;
    if (els.huntMapList) els.huntMapList.innerHTML = "";
    if (els.huntMapEmpty) els.huntMapEmpty.hidden = true;
  }

  function matchQueryToTechniques(query) {
    const q = (query || "").trim().toLowerCase();
    if (!q) return [];
    const techniques = (state.itmCatalog && state.itmCatalog.techniques) || [];
    const scored = [];
    const seen = new Set();

    const scoreTech = (tech) => {
      const id = String(tech.id || "").toLowerCase();
      const title = String(tech.title || "").toLowerCase();
      const aliases = techniqueAliases(tech).map((a) => String(a).toLowerCase());
      let score = 0;

      if (id === q) score = 100;
      else if (aliases.some((a) => a === q)) score = 92;
      else if (title === q) score = 88;
      else {
        const aliasHits = aliases
          .filter(
            (a) =>
              a.includes(q) || (q.length >= 3 && a.length >= 3 && q.includes(a)),
          )
          .sort((a, b) => b.length - a.length);
        if (aliasHits.length) {
          score = 55 + Math.min(25, aliasHits[0].length);
        } else if (title.includes(q) || id.includes(q)) {
          score = 40;
        } else if (
          q.length >= 3 &&
          (title.includes(q) || aliases.some((a) => a.includes(q)))
        ) {
          score = 35;
        }
      }

      if (score > 0) {
        const key = String(tech.id).toUpperCase();
        if (seen.has(key)) return;
        seen.add(key);
        scored.push({
          id: tech.id,
          title: tech.title,
          theme: tech.theme,
          score,
          articleCount: 0,
          fromCatalog: true,
        });
      }
    };

    techniques.forEach(scoreTech);

    // Fallback when /itm is slow/unavailable or snapshot lags aliases.
    Object.entries(CLIENT_ALIAS_EXTRAS).forEach(([id, meta]) => {
      if (seen.has(id)) return;
      scoreTech({
        id,
        title: meta.title,
        theme: meta.theme,
        aliases: meta.aliases,
      });
    });

    return scored.sort((a, b) => b.score - a.score || a.id.localeCompare(b.id));
  }

  function aggregateItmFromArticles(articles) {
    const byId = new Map();
    (articles || []).forEach((article) => {
      (article.itm_hits || []).forEach((hit) => {
        const id = hit && hit.id;
        if (!id) return;
        const prev = byId.get(id) || {
          id,
          title: hit.title || id,
          theme: hit.theme || "",
          articleCount: 0,
          fromCatalog: false,
          score: 0,
        };
        prev.articleCount += 1;
        if (hit.title) prev.title = hit.title;
        if (hit.theme) prev.theme = hit.theme;
        byId.set(id, prev);
      });
    });
    return byId;
  }

  function buildHuntMapEntries(query, articles) {
    const catalog = matchQueryToTechniques(query);
    const fromArticles = aggregateItmFromArticles(articles);
    const byId = new Map();

    catalog.forEach((entry) => {
      const corp = fromArticles.get(entry.id);
      byId.set(entry.id, {
        ...entry,
        articleCount: corp ? corp.articleCount : 0,
      });
    });

    [...fromArticles.values()]
      .sort((a, b) => b.articleCount - a.articleCount || a.id.localeCompare(b.id))
      .forEach((entry) => {
        if (byId.has(entry.id)) return;
        byId.set(entry.id, {
          ...entry,
          score: 10 + entry.articleCount,
          fromCatalog: false,
        });
      });

    return [...byId.values()].sort((a, b) => {
      if (a.fromCatalog !== b.fromCatalog) return a.fromCatalog ? -1 : 1;
      if (a.fromCatalog) return b.score - a.score || a.id.localeCompare(b.id);
      return b.articleCount - a.articleCount || a.id.localeCompare(b.id);
    });
  }

  function renderHuntMap(query, articles) {
    if (!els.huntMap || !els.huntMapList) return;
    const entries = buildHuntMapEntries(query, articles);
    els.huntMap.hidden = false;
    els.huntMapList.innerHTML = "";

    if (!entries.length) {
      if (els.huntMapEmpty) els.huntMapEmpty.hidden = false;
      return;
    }
    if (els.huntMapEmpty) els.huntMapEmpty.hidden = true;

    entries.slice(0, 24).forEach((entry) => {
      const li = document.createElement("li");
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "chip signal";
      btn.title = entry.theme
        ? `${entry.id} · ${entry.theme}`
        : `${entry.id} · open in matrix`;
      const label = document.createElement("span");
      label.textContent = `${entry.id} · ${entry.title}`;
      btn.appendChild(label);
      if (entry.articleCount > 0) {
        const count = document.createElement("span");
        count.className = "hunt-map-count";
        count.textContent = `×${entry.articleCount}`;
        btn.appendChild(count);
      } else {
        const count = document.createElement("span");
        count.className = "hunt-map-count hunt-map-count-zero";
        count.textContent = "×0";
        btn.appendChild(count);
      }
      btn.addEventListener("click", () => {
        selectTechnique(entry.id).catch((err) =>
          setStatus(`Load failed: ${err.message}`),
        );
      });
      li.appendChild(btn);
      els.huntMapList.appendChild(li);
    });
  }

  function clearWorkbench() {
    state.selectedLink = null;
    els.panelEmpty.hidden = false;
    els.panelBody.hidden = true;
    syncBoardToggle();
  }

  function renderMatrixColumns() {
    if (!els.matrixColumns) return;
    let techniques = (state.itmCatalog && state.itmCatalog.techniques) || [];
    if (state.searchMode && state.huntMappedIds.length) {
      const allow = new Set(state.huntMappedIds.map((id) => String(id).toUpperCase()));
      techniques = techniques.filter((t) => allow.has(String(t.id).toUpperCase()));
    }
    const query = (state.matrixQuery || "").trim();
    const coverageFirst = state.searchMode && state.huntMappedIds.length > 0;
    els.matrixColumns.innerHTML = "";

    MATRIX_THEMES.forEach((theme) => {
      let parents = techniques.filter(
        (t) => t.theme === theme.id && !t.parent_id,
      );
      if (coverageFirst) {
        parents = [...parents].sort(
          (a, b) => Number(techniqueHasCoverage(b)) - Number(techniqueHasCoverage(a)),
        );
      }
      const visibleParents = parents.filter((parent) => {
        const kids = childrenOf(parent.id).filter((k) =>
          techniques.some((t) => t.id === k.id),
        );
        const parentMatch = techniqueMatchesQuery(parent, query);
        const matchingKids = kids.filter((k) => techniqueMatchesQuery(k, query));
        return !query || parentMatch || matchingKids.length > 0;
      });

      if (coverageFirst && !visibleParents.length && !query) {
        return;
      }

      const details = document.createElement("details");
      details.className = "matrix-col";
      details.dataset.theme = theme.id;
      const forceOpen = Boolean(query) && visibleParents.length > 0;
      details.open = forceOpen || !state.collapsedThemes.has(theme.id);
      details.addEventListener("toggle", () => {
        if (details.open) state.collapsedThemes.delete(theme.id);
        else state.collapsedThemes.add(theme.id);
      });

      const summary = document.createElement("summary");
      summary.className = "matrix-col-summary";
      const label = document.createElement("span");
      label.textContent = theme.label;
      const count = document.createElement("span");
      count.className = "matrix-col-count";
      count.textContent = String(visibleParents.length);
      summary.append(label, count);
      details.appendChild(summary);

      const list = document.createElement("ul");
      list.className = "matrix-tech-list";

      visibleParents.forEach((parent) => {
        let kids = childrenOf(parent.id);
        if (state.searchMode && state.huntMappedIds.length) {
          const allow = new Set(state.huntMappedIds.map((id) => String(id).toUpperCase()));
          kids = kids.filter((k) => allow.has(String(k.id).toUpperCase()));
        }
        const parentMatch = techniqueMatchesQuery(parent, query);
        const matchingKids = kids.filter((k) => techniqueMatchesQuery(k, query));

        const expanded =
          state.expandedParents.has(parent.id) ||
          (Boolean(query) && matchingKids.length > 0);

        const li = document.createElement("li");
        li.className = "matrix-tech-item";

        const row = document.createElement("div");
        row.className = "matrix-tech-row";

        if (kids.length) {
          const toggle = document.createElement("button");
          toggle.type = "button";
          toggle.className = "matrix-expand";
          toggle.setAttribute("aria-expanded", expanded ? "true" : "false");
          toggle.textContent = expanded ? "−" : "+";
          toggle.title = expanded ? "Collapse subsections" : "Expand subsections";
          toggle.addEventListener("click", (event) => {
            event.stopPropagation();
            if (state.expandedParents.has(parent.id)) {
              state.expandedParents.delete(parent.id);
            } else {
              state.expandedParents.add(parent.id);
            }
            renderMatrixColumns();
          });
          row.appendChild(toggle);
        } else {
          const spacer = document.createElement("span");
          spacer.className = "matrix-expand-spacer";
          row.appendChild(spacer);
        }

        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "matrix-tech-btn";
        if (!techniqueHasCoverage(parent)) btn.classList.add("matrix-tech-zero");
        if (parent.id === state.selectedTechniqueId) btn.classList.add("active");
        const idSpan = document.createElement("span");
        idSpan.className = "matrix-tech-id";
        idSpan.textContent = parent.id;
        const titleSpan = document.createElement("span");
        titleSpan.className = "matrix-tech-title";
        titleSpan.textContent = techniqueTitleWithCount(parent);
        btn.append(idSpan, titleSpan);
        btn.addEventListener("click", () => {
          selectTechnique(parent.id).catch((err) =>
            setStatus(`Technique load failed: ${err.message}`),
          );
        });
        row.appendChild(btn);
        li.appendChild(row);

        if (expanded && kids.length) {
          const sub = document.createElement("ul");
          sub.className = "matrix-sub-list";
          const visibleKids = query ? matchingKids : kids;
          visibleKids.forEach((kid) => {
            const subLi = document.createElement("li");
            const subBtn = document.createElement("button");
            subBtn.type = "button";
            subBtn.className = "matrix-tech-btn matrix-tech-sub";
            if (!techniqueHasCoverage(kid)) subBtn.classList.add("matrix-tech-zero");
            if (kid.id === state.selectedTechniqueId) subBtn.classList.add("active");
            const kidId = document.createElement("span");
            kidId.className = "matrix-tech-id";
            kidId.textContent = kid.id;
            const kidTitle = document.createElement("span");
            kidTitle.className = "matrix-tech-title";
            kidTitle.textContent = techniqueTitleWithCount(kid);
            subBtn.append(kidId, kidTitle);
            subBtn.addEventListener("click", () => {
              selectTechnique(kid.id).catch((err) =>
                setStatus(`Technique load failed: ${err.message}`),
              );
            });
            subLi.appendChild(subBtn);
            sub.appendChild(subLi);
          });
          li.appendChild(sub);
        }

        list.appendChild(li);
      });

      if (!list.children.length) {
        const empty = document.createElement("li");
        empty.className = "matrix-col-empty";
        empty.textContent = query ? "No matches" : "No techniques";
        list.appendChild(empty);
      }

      details.appendChild(list);
      els.matrixColumns.appendChild(details);
    });
  }

  function renderMatrixControlList(kind) {
    if (!els.matrixControlList) return;
    const items =
      kind === "preventions"
        ? (state.itmCatalog && state.itmCatalog.preventions) || []
        : (state.itmCatalog && state.itmCatalog.detections) || [];
    const selectedId =
      kind === "preventions" ? state.selectedPreventionId : state.selectedDetectionId;
    const query = (state.matrixQuery || "").trim().toLowerCase();

    els.matrixControlList.innerHTML = "";
    items
      .filter((item) => {
        if (!query) return true;
        return (
          String(item.id || "").toLowerCase().includes(query) ||
          String(item.title || "").toLowerCase().includes(query)
        );
      })
      .forEach((item) => {
        const li = document.createElement("li");
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "matrix-control-btn";
        if (item.id === selectedId) btn.classList.add("active");
        const idSpan = document.createElement("span");
        idSpan.className = "matrix-tech-id";
        idSpan.textContent = item.id;
        const titleSpan = document.createElement("span");
        titleSpan.className = "matrix-tech-title";
        titleSpan.textContent = item.title;
        btn.append(idSpan, titleSpan);
        btn.addEventListener("click", () => {
          if (kind === "preventions") {
            selectPrevention(item.id).catch((err) =>
              setStatus(`Prevention load failed: ${err.message}`),
            );
          } else {
            selectDetection(item.id).catch((err) =>
              setStatus(`Detection load failed: ${err.message}`),
            );
          }
        });
        li.appendChild(btn);
        els.matrixControlList.appendChild(li);
      });

    if (!els.matrixControlList.children.length) {
      const li = document.createElement("li");
      li.className = "matrix-col-empty";
      li.textContent = query ? "No matches" : "No controls";
      els.matrixControlList.appendChild(li);
    }
  }

  function renderMatrixBrowse() {
    const mode = state.matrixMode || "techniques";
    const isTech = mode === "techniques";
    if (els.matrixColumns) els.matrixColumns.hidden = !isTech;
    if (els.matrixControlList) els.matrixControlList.hidden = isTech;
    if (els.matrixQ) {
      els.matrixQ.placeholder =
        mode === "techniques"
          ? "Search techniques (id or title)…"
          : mode === "detections"
            ? "Search detections (id or title)…"
            : "Search preventions (id or title)…";
    }
    document.querySelectorAll("#matrix-mode-tabs .matrix-mode-tab").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.matrixMode === mode);
    });
    if (els.matrixLatest) {
      els.matrixLatest.classList.toggle("active", !hasMatrixFilter());
    }
    if (isTech) renderMatrixColumns();
    else renderMatrixControlList(mode === "preventions" ? "preventions" : "detections");
  }

  async function showLatest() {
    navigate("/");
    setView("stream");
    state.selectedTechniqueId = null;
    state.selectedDetectionId = null;
    state.selectedPreventionId = null;
    state.linkedTechniques = [];
    state.searchMode = false;
    state.lastHuntQuery = "";
    state.huntMappedIds = [];
    if (els.q) els.q.value = "";
    if (els.clearSearch) els.clearSearch.hidden = true;
    clearHuntMap();
    updateFilterContext("");
    await ensureItmCatalog(true);
    renderMatrixBrowse();
    clearWorkbench();
    await loadSources();
    await loadArticles();
    setStatus(`Latest · ${state.lastTotalIndexed} indexed`);
    syncHuntUsecases();
    renderFilterCrumbs();
  }

  function channelParam() {
    return state.channel && state.channel !== "all" ? state.channel : undefined;
  }

  function useCaseParam() {
    return state.useCase && state.useCase !== "all" ? state.useCase : undefined;
  }

  function insiderTypeParam() {
    return state.insiderType && state.insiderType !== "all" ? state.insiderType : undefined;
  }

  async function selectTechnique(techniqueId) {
    navigate(`/technique/${encodeURIComponent(techniqueId)}`);
    await showDossier(techniqueId);
  }

  function renderDossierShell(tech) {
    if (els.dossierTitle) els.dossierTitle.textContent = `${tech.id} · ${tech.title}`;
    if (els.dossierMeta) {
      const themeLabel =
        (MATRIX_THEMES.find((t) => t.id === tech.theme) || {}).label || tech.theme || "";
      const count =
        typeof tech.article_count === "number" ? ` · ${tech.article_count} indexed` : "";
      els.dossierMeta.textContent = `${themeLabel}${count}`;
    }
    if (els.dossierDesc) {
      const desc = String(tech.description || "").trim();
      els.dossierDesc.textContent = desc;
      els.dossierDesc.hidden = !desc;
    }
    if (els.dossierItmLink) els.dossierItmLink.href = itmUrl(tech);
    fillCopyableChips(els.dossierTermList, techniqueAliases(tech), true);
    renderQueryBlocks(
      els.dossierQueries,
      buildHuntQueries({ terms: techniqueAliases(tech) }),
    );
    fillControlChips(els.dossierDetectionList, tech.detections, "detection");
    fillControlChips(els.dossierPreventionList, tech.preventions, "prevention");
    if (els.dossierArticleList) els.dossierArticleList.innerHTML = "";
    if (els.dossierCaseCount) els.dossierCaseCount.textContent = "";
  }

  function renderDossierArticles(dataOrResults) {
    if (!els.dossierArticleList) return;
    const clusters = clustersFromResponse(dataOrResults);
    state.clusters = clusters;
    state.articles = flattenClusterMembers(clusters);
    els.dossierArticleList.innerHTML = "";
    state.cursorIndex = -1;
    if (els.dossierCaseCount) {
      els.dossierCaseCount.textContent = `(${clusters.length})`;
    }
    if (!clusters.length) {
      const empty = document.createElement("li");
      empty.className = "panel-empty stream-empty";
      empty.textContent = "No indexed cases for this technique yet.";
      els.dossierArticleList.appendChild(empty);
      return;
    }
    clusters.forEach((cluster) => {
      els.dossierArticleList.appendChild(buildArticleRow(cluster));
    });
  }

  async function showDossier(techniqueId) {
    await ensureItmCatalog();
    state.matrixMode = "techniques";
    state.selectedTechniqueId = techniqueId;
    state.selectedDetectionId = null;
    state.selectedPreventionId = null;
    state.linkedTechniques = [techniqueId];
    state.searchMode = false;
    state.lastHuntQuery = "";
    state.huntMappedIds = [];
    if (els.q) els.q.value = "";
    if (els.clearSearch) els.clearSearch.hidden = true;
    clearHuntMap();
    renderMatrixBrowse();
    syncHuntUsecases();

    const tech = selectedTechnique();
    if (!tech) {
      state.selectedTechniqueId = null;
      updateFilterContext("");
      setView("stream");
      setStatus(`Unknown technique “${techniqueId}”`);
      return;
    }

    state.dossierTechniqueId = tech.id;
    updateFilterContext("");
    setStatus(`Loading dossier for ${tech.id}…`);
    setActivePane("articles");
    setView("dossier");
    if (els.streamTitle) els.streamTitle.textContent = `${tech.id} dossier`;
    if (els.streamCount) els.streamCount.textContent = "";
    renderDossierShell(tech);
    const data = await api("/articles", {
      limit: 50,
      min_score: 0,
      itm_alignment: "all",
      channel: channelParam(),
      use_case: useCaseParam(),
      insider_type: insiderTypeParam(),
      source_id: state.sourceId || undefined,
      itm_id: tech.id,
      topic_match: true,
    });
    state.lastTotalIndexed = data.total_indexed || state.lastTotalIndexed;
    clearWorkbench();
    renderDossierArticles(data);
    setStatus(`${tech.id} · ${(data.clusters || data.results || []).length} related stories`);
  }

  async function selectDetection(detectionId) {
    navigate("/");
    setView("stream");
    await ensureItmCatalog();
    state.matrixMode = "detections";
    state.selectedDetectionId = detectionId;
    state.selectedTechniqueId = null;
    state.selectedPreventionId = null;
    state.searchMode = false;
    if (els.q) els.q.value = "";
    if (els.clearSearch) els.clearSearch.hidden = true;
    clearHuntMap();
    renderMatrixBrowse();

    const control = ((state.itmCatalog && state.itmCatalog.detections) || []).find(
      (c) => c.id === detectionId,
    );
    if (!control) {
      updateFilterContext("");
      return;
    }

    const linked = linkedTechniqueIdsForControl("detection", detectionId);
    state.linkedTechniques = linked;
    updateFilterContext("");
    renderFilterCrumbs();
    setStatus(`Loading articles for ${control.id}…`);
    setActivePane("articles");
    const data = await api("/articles", {
      limit: 50,
      min_score: 0,
      itm_alignment: "all",
      channel: channelParam(),
      use_case: useCaseParam(),
      insider_type: insiderTypeParam(),
      source_id: state.sourceId || undefined,
      detection_id: control.id,
      topic_match: true,
    });
    state.lastTotalIndexed = data.total_indexed || state.lastTotalIndexed;
    clearWorkbench();
    renderArticles(data, `${control.id} · ${control.title}`);
    setStatus(`${control.id} · ${(data.clusters || data.results || []).length} related stories`);
  }

  async function selectPrevention(preventionId) {
    navigate("/");
    setView("stream");
    await ensureItmCatalog();
    state.matrixMode = "preventions";
    state.selectedPreventionId = preventionId;
    state.selectedTechniqueId = null;
    state.selectedDetectionId = null;
    state.searchMode = false;
    if (els.q) els.q.value = "";
    if (els.clearSearch) els.clearSearch.hidden = true;
    clearHuntMap();
    renderMatrixBrowse();

    const control = ((state.itmCatalog && state.itmCatalog.preventions) || []).find(
      (c) => c.id === preventionId,
    );
    if (!control) {
      updateFilterContext("");
      return;
    }

    const linked = linkedTechniqueIdsForControl("prevention", preventionId);
    state.linkedTechniques = linked;
    updateFilterContext("");
    renderFilterCrumbs();
    setStatus(`Loading articles for ${control.id}…`);
    setActivePane("articles");
    const data = await api("/articles", {
      limit: 50,
      min_score: 0,
      itm_alignment: "all",
      channel: channelParam(),
      use_case: useCaseParam(),
      insider_type: insiderTypeParam(),
      source_id: state.sourceId || undefined,
      prevention_id: control.id,
      topic_match: true,
    });
    state.lastTotalIndexed = data.total_indexed || state.lastTotalIndexed;
    clearWorkbench();
    renderArticles(data, `${control.id} · ${control.title}`);
    setStatus(`${control.id} · ${(data.clusters || data.results || []).length} related stories`);
  }

  function selectArticle(article) {
    state.selectedLink = article.link;
    document.querySelectorAll(".article-item").forEach((btn) => {
      const links = (btn.dataset.links || btn.dataset.link || "")
        .split("|")
        .filter(Boolean);
      btn.classList.toggle("active", links.includes(article.link));
    });
    document.querySelectorAll(".cluster-source").forEach((chip) => {
      chip.classList.toggle("active", chip.dataset.link === article.link);
    });

    els.panelEmpty.hidden = true;
    els.panelBody.hidden = false;
    els.panelTitle.textContent = article.title;
    els.panelMeta.textContent = `${article.source_name} · ${formatDate(article.published)}`;
    els.panelLink.href = article.link;
    fillCopyableChips(els.operatorList, composeOperatorTerms(article), true);
    fillItmChips(els.itmList, article.itm_hits);
    fillControlChips(els.detectionList, article.related_detections, "detection");
    syncBoardToggle();

    if (isMobileLayout()) setActivePane("workbench");
  }

  function buildArticleRow(cluster) {
    const article = cluster.primary;
    const siblings = cluster.siblings || [];
    const members = [article, ...siblings];
    const li = document.createElement("li");
    li.className = "article-row";
    const storyKey = clusterKey(cluster);
    li.dataset.storyKey = storyKey;
    if (state.dismissed.has(storyKey)) li.classList.add("dismissed");

    const boardBtn = document.createElement("button");
    boardBtn.type = "button";
    boardBtn.className = "article-board-btn";
    boardBtn.dataset.link = article.link;
    const onBoard = articleOnBoard(article.link);
    boardBtn.classList.toggle("on-board", onBoard);
    boardBtn.textContent = onBoard ? "✓" : "+";
    boardBtn.title = onBoard ? "Remove from board" : "Add to extraction board";
    boardBtn.setAttribute("aria-label", boardBtn.title);
    boardBtn.addEventListener("click", (event) => {
      event.stopPropagation();
      if (articleOnBoard(article.link)) removeFromBoard(article.link);
      else addToBoard(article, { focusWorkbench: true });
    });

    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "article-item";
    btn.dataset.link = article.link;
    btn.dataset.links = members.map((m) => m.link).join("|");
    if (members.some((m) => m.link === state.selectedLink)) {
      btn.classList.add("active");
    }

    const h3 = document.createElement("h3");
    h3.textContent = article.title;
    const meta = document.createElement("p");
    meta.className = "row-meta";
    const extra =
      cluster.member_count > 1 ? ` · +${cluster.member_count - 1} sources` : "";
    meta.textContent = `${article.source_name}${extra} · ${formatDate(article.published)}`;
    if (article.insider_type) {
      const badge = document.createElement("span");
      badge.className = `insider-type-badge insider-type-${article.insider_type}`;
      badge.textContent = INSIDER_TYPE_LABELS[article.insider_type] || article.insider_type;
      meta.append(" · ", badge);
    }
    (article.use_cases || []).forEach((useCaseId) => {
      const tag = document.createElement("span");
      tag.className = "use-case-tag";
      tag.textContent = USE_CASE_LABELS[useCaseId] || useCaseId;
      meta.append(" ", tag);
    });
    const snip = document.createElement("p");
    snip.className = "snip";
    snip.textContent = article.summary || "";

    const preview = document.createElement("div");
    preview.className = "kw-preview";
    const opPreview = composeOperatorTerms(article).slice(0, 4);
    const itmPreview = (article.itm_hits || []).slice(0, 3);
    if (opPreview.length) {
      opPreview.forEach((term) => {
        const chip = document.createElement("span");
        chip.className = "chip signal";
        chip.textContent = term;
        preview.appendChild(chip);
      });
    } else if (itmPreview.length) {
      itmPreview.forEach((hit) => {
        const chip = document.createElement("span");
        chip.className = "chip signal";
        chip.textContent = hit.id;
        preview.appendChild(chip);
      });
    }

    btn.append(h3, meta, snip, preview);

    if (siblings.length) {
      const sources = document.createElement("div");
      sources.className = "cluster-sources";
      sources.setAttribute("role", "group");
      sources.setAttribute("aria-label", "Other sources for this story");
      members.forEach((member) => {
        const chip = document.createElement("button");
        chip.type = "button";
        chip.className = "cluster-source";
        chip.dataset.link = member.link;
        chip.textContent = member.source_name;
        if (member.link === state.selectedLink) chip.classList.add("active");
        chip.addEventListener("click", (event) => {
          event.stopPropagation();
          selectArticle(member);
        });
        sources.appendChild(chip);
      });
      btn.appendChild(sources);
    }

    btn.addEventListener("click", () => selectArticle(article));
    li.append(boardBtn, btn);
    return li;
  }

  function renderArticles(dataOrResults, title, options = {}) {
    const clusters = clustersFromResponse(dataOrResults);
    state.clusters = clusters;
    state.articles = flattenClusterMembers(clusters);
    els.streamTitle.textContent = title;
    els.streamCount.textContent = `${clusters.length} shown`;
    els.articleList.innerHTML = "";

    state.cursorIndex = -1;
    if (!clusters.length) {
      const empty = document.createElement("li");
      empty.className = "panel-empty stream-empty";
      empty.textContent = emptyMessage(options.huntEmpty || "");
      els.articleList.appendChild(empty);
      return;
    }

    clusters.forEach((cluster) => {
      els.articleList.appendChild(buildArticleRow(cluster));
    });
  }

  function activeArticleListEl() {
    return state.view === "dossier" ? els.dossierArticleList : els.articleList;
  }

  function cursorCluster() {
    return state.clusters[state.cursorIndex] || null;
  }

  function moveCursor(delta) {
    const listEl = activeArticleListEl();
    if (!listEl) return;
    const rows = [...listEl.querySelectorAll(".article-row")];
    if (!rows.length) return;
    const next = Math.min(rows.length - 1, Math.max(0, state.cursorIndex + delta));
    if (state.cursorIndex >= 0 && rows[state.cursorIndex]) {
      rows[state.cursorIndex].classList.remove("cursor");
    }
    state.cursorIndex = next;
    rows[next].classList.add("cursor");
    rows[next].scrollIntoView({ block: "nearest" });
  }

  document.addEventListener("keydown", (event) => {
    if (event.metaKey || event.ctrlKey || event.altKey) return;
    const target = event.target;
    const tag = target && target.tagName;
    if (
      tag === "INPUT" ||
      tag === "SELECT" ||
      tag === "TEXTAREA" ||
      (target && target.isContentEditable)
    ) {
      return;
    }
    const key = event.key.toLowerCase();
    if (event.key === "/") {
      event.preventDefault();
      if (els.q) els.q.focus();
      return;
    }
    if (key === "j" || key === "k") {
      event.preventDefault();
      moveCursor(key === "j" ? 1 : -1);
      return;
    }
    const cluster = cursorCluster();
    if (!cluster || !cluster.primary) return;
    if (event.key === "Enter") {
      event.preventDefault();
      selectArticle(cluster.primary);
    } else if (key === "o") {
      event.preventDefault();
      window.open(cluster.primary.link, "_blank", "noopener");
    } else if (key === "x") {
      event.preventDefault();
      if (articleOnBoard(cluster.primary.link)) removeFromBoard(cluster.primary.link);
      else addToBoard(cluster.primary);
    } else if (key === "d") {
      event.preventDefault();
      toggleDismissed(cluster);
    }
  });

  function setActiveScopePill(alignment) {
    document.querySelectorAll("#align-filters .pill").forEach((btn) => {
      btn.classList.toggle("active", (btn.dataset.alignment || "") === alignment);
    });
  }

  function setActiveChannelPill(channel) {
    document.querySelectorAll("#channel-filters .pill").forEach((btn) => {
      btn.classList.toggle("active", (btn.dataset.channel || "") === channel);
    });
  }

  function setActiveInsiderTypePill(insiderType) {
    document.querySelectorAll("#insider-type-filters .pill").forEach((btn) => {
      btn.classList.toggle("active", (btn.dataset.insiderType || "") === insiderType);
    });
  }

  const REFINE_OPEN_KEY = "insider-intel-refine-open";

  function buildCrumb(label, onRemove, title) {
    const li = document.createElement("li");
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "chip crumb";
    btn.title = title || `Remove filter: ${label}`;
    const text = document.createElement("span");
    text.textContent = label;
    const x = document.createElement("span");
    x.className = "crumb-x";
    x.setAttribute("aria-hidden", "true");
    x.textContent = "×";
    btn.append(text, x);
    btn.addEventListener("click", onRemove);
    li.appendChild(btn);
    return li;
  }

  function renderFilterCrumbs() {
    if (!els.filterCrumbs) return;
    els.filterCrumbs.innerHTML = "";
    const fail = (err) => setStatus(`Load failed: ${err.message}`);
    const items = [];

    if (state.itmAlignment === "all") {
      items.push(
        buildCrumb(
          "All indexed",
          () => {
            state.itmAlignment = "insider";
            setActiveScopePill("insider");
            updateRefineSummary();
            reapplyActiveFilters().catch(fail);
          },
          "Back to Insider Focus",
        ),
      );
    }

    if (state.channel && state.channel !== "all") {
      const labels = { news: "News", filings: "Filings", tips: "Tips", social: "Social" };
      items.push(
        buildCrumb(labels[state.channel] || state.channel, () => {
          state.channel = "all";
          setActiveChannelPill("all");
          updateRefineSummary();
          reapplyActiveFilters().catch(fail);
        }),
      );
    }

    if (state.useCase) {
      items.push(
        buildCrumb(USE_CASE_LABELS[state.useCase] || state.useCase, () => {
          state.useCase = "";
          syncHuntUsecases();
          updateRefineSummary();
          reapplyActiveFilters().catch(fail);
        }),
      );
    }

    if (state.insiderType && state.insiderType !== "all") {
      items.push(
        buildCrumb(INSIDER_TYPE_LABELS[state.insiderType] || state.insiderType, () => {
          state.insiderType = "all";
          setActiveInsiderTypePill("all");
          updateRefineSummary();
          reapplyActiveFilters().catch(fail);
        }),
      );
    }

    if (state.sourceId) {
      let label = state.sourceId;
      const opt = els.sourceSelect && els.sourceSelect.selectedOptions[0];
      if (opt) label = opt.textContent.replace(/\s*\(\d+\)\s*$/, "").trim();
      items.push(
        buildCrumb(label, () => {
          state.sourceId = "";
          if (els.sourceSelect) els.sourceSelect.value = "";
          updateRefineSummary();
          reapplyActiveFilters().catch(fail);
        }),
      );
    }

    if (state.selectedDetectionId || state.selectedPreventionId) {
      const kind = state.selectedDetectionId ? "detections" : "preventions";
      const id = state.selectedDetectionId || state.selectedPreventionId;
      const control = ((state.itmCatalog && state.itmCatalog[kind]) || []).find(
        (c) => c.id === id,
      );
      items.push(
        buildCrumb(control ? `${control.id} · ${control.title}` : id, () => {
          showLatest().catch(fail);
        }),
      );
    }

    if (state.searchMode && state.lastHuntQuery) {
      items.push(
        buildCrumb(`Hunt: ${state.lastHuntQuery}`, () => {
          showLatest().catch(fail);
        }),
      );
    }

    items.forEach((li) => els.filterCrumbs.appendChild(li));
    els.filterCrumbs.hidden = items.length === 0;
  }

  function updateRefineSummary() {
    if (!els.refineState) return;
    const alignLabel =
      state.itmAlignment === "all" ? "All indexed" : "Insider";
    let channelLabel = "All channels";
    if (state.channel === "news") channelLabel = "News";
    else if (state.channel === "filings") channelLabel = "Filings";
    else if (state.channel === "tips") channelLabel = "Tips";
    else if (state.channel === "social") channelLabel = "Social";
    let sourceLabel = "";
    if (state.sourceId && els.sourceSelect) {
      const opt = els.sourceSelect.selectedOptions[0];
      const raw = (opt && opt.textContent) || state.sourceId;
      sourceLabel = raw.replace(/\s*\(\d+\)\s*$/, "").trim();
    }
    const parts = [alignLabel, channelLabel];
    if (state.useCase) parts.push(USE_CASE_LABELS[state.useCase] || state.useCase);
    if (state.insiderType && state.insiderType !== "all") {
      parts.push(INSIDER_TYPE_LABELS[state.insiderType] || state.insiderType);
    }
    if (sourceLabel) parts.push(sourceLabel);
    els.refineState.textContent = parts.join(" · ");
    renderFilterCrumbs();
  }

  function initRefinePanel() {
    if (!els.refinePanel) return;
    const applyOpen = () => {
      if (!isMobileLayout()) {
        els.refinePanel.open = true;
        return;
      }
      const saved = sessionStorage.getItem(REFINE_OPEN_KEY);
      els.refinePanel.open = saved === "1";
    };
    applyOpen();
    els.refinePanel.addEventListener("toggle", () => {
      if (!isMobileLayout()) return;
      sessionStorage.setItem(
        REFINE_OPEN_KEY,
        els.refinePanel.open ? "1" : "0",
      );
    });
    MOBILE_MQ.addEventListener("change", applyOpen);
    updateRefineSummary();
  }

  function streamTitle() {
    return state.sourceId ? "Source feed" : "Latest";
  }

  async function loadSources() {
    const sources = await api("/sources", {
      min_score: UI_MIN_SCORE,
      itm_alignment: state.itmAlignment,
      channel: channelParam(),
      use_case: useCaseParam(),
      insider_type: insiderTypeParam(),
      theme: state.theme || undefined,
    });

    const ids = new Set(sources.map((s) => s.id));
    if (state.sourceId && !ids.has(state.sourceId)) {
      state.sourceId = "";
    }

    const totalMatching = sources.reduce(
      (sum, source) => sum + (source.article_count || 0),
      0,
    );

    if (els.sourceSelect) {
      els.sourceSelect.innerHTML = "";
      const allOpt = document.createElement("option");
      allOpt.value = "";
      allOpt.textContent = `All feeds (${totalMatching})`;
      els.sourceSelect.appendChild(allOpt);
      sources.forEach((source) => {
        const opt = document.createElement("option");
        opt.value = source.id;
        const count =
          typeof source.article_count === "number" ? ` (${source.article_count})` : "";
        opt.textContent = `${source.name}${count}`;
        els.sourceSelect.appendChild(opt);
      });
      els.sourceSelect.value = state.sourceId || "";
    }
    updateRefineSummary();
  }

  async function loadArticles() {
    setStatus(`Loading stream from ${apiBase}…`);
    const data = await api("/articles", {
      limit: 75,
      min_score: UI_MIN_SCORE,
      itm_alignment: state.itmAlignment,
      channel: channelParam(),
      use_case: useCaseParam(),
      insider_type: insiderTypeParam(),
      source_id: state.sourceId || undefined,
      theme: state.theme || undefined,
    });
    state.lastTotalIndexed = data.total_indexed || 0;
    clearHuntMap();
    updateFilterContext("");
    renderArticles(data, streamTitle());
    setStatus(`API ok · ${data.total_indexed} indexed`);
  }

  async function reloadStreamOrSearch() {
    if (hasMatrixFilter()) {
      if (state.selectedTechniqueId) {
        await selectTechnique(state.selectedTechniqueId);
      } else if (state.selectedDetectionId) {
        await selectDetection(state.selectedDetectionId);
      } else if (state.selectedPreventionId) {
        await selectPrevention(state.selectedPreventionId);
      }
      return;
    }
    await loadSources();
    if (state.searchMode && els.q.value.trim()) {
      await runSearch(els.q.value.trim());
    } else {
      await loadArticles();
    }
  }

  async function refreshStream() {
    if (els.refreshStream) els.refreshStream.disabled = true;
    setStatus("Refreshing…");
    try {
      const reload = await api("/reload", {}, { method: "POST" });
      state.itmCatalog = null;
      state.itmCatalogKey = "";
      await ensureItmCatalog(true);
      renderMatrixBrowse();
      await reapplyActiveFilters();
      if (state.dataState) {
        state.dataState.indexed = reload.indexed_articles ?? state.dataState.indexed;
        renderDataState();
      }
      setStatus(`Refreshed · ${reload.indexed_articles ?? state.lastTotalIndexed} indexed`);
    } catch (err) {
      setStatus(`Refresh failed: ${err.message}`);
    } finally {
      if (els.refreshStream) els.refreshStream.disabled = false;
    }
  }

  async function runSearch(query) {
    navigate("/");
    setView("stream");
    state.selectedTechniqueId = null;
    state.selectedDetectionId = null;
    state.selectedPreventionId = null;
    state.linkedTechniques = [];
    updateFilterContext("");
    setStatus(`Mapping “${query}”…`);
    state.searchMode = true;
    state.lastHuntQuery = query;
    els.clearSearch.hidden = false;
    await ensureItmCatalog(true);
    const mapped = matchQueryToTechniques(query);
    state.huntMappedIds = mapped.map((m) => m.id);
    renderMatrixBrowse();

    const [tagData, hybrid] = await Promise.all([
      fetchArticlesForTechniqueIds(state.huntMappedIds, { limit: 50 }),
      api("/search", {
        q: query,
        mode: "hybrid",
        limit: 40,
        min_score: 0,
        itm_alignment: "all",
        channel: channelParam(),
        use_case: useCaseParam(),
        insider_type: insiderTypeParam(),
        source_id: state.sourceId || undefined,
        theme: state.theme || undefined,
      }),
    ]);

    const results = mergeArticleLists(tagData.results || [], hybrid.results || []);
    state.lastTotalIndexed =
      tagData.total_indexed || hybrid.total_indexed || state.lastTotalIndexed;
    clearWorkbench();
    renderHuntMap(query, results);
    const title =
      state.huntMappedIds.length > 0
        ? `Hunt: ${query} · ${state.huntMappedIds.join(", ")}`
        : `Hunt: ${query}`;
    renderArticles(
      {
        results,
        clusters: [],
        total_indexed: state.lastTotalIndexed,
        count: results.length,
      },
      title,
      {
        huntEmpty:
          results.length === 0 && state.huntMappedIds.length > 0
            ? `Mapped to ${state.huntMappedIds.join(", ")} — no indexed stories yet for this Source/Channel. Try Filings, clear Source, or Refresh after ingest.`
            : results.length === 0 && state.huntMappedIds.length === 0
              ? `No ITM map for “${query}”. Add an alias in shared/itm/aliases.py if this is a real insider-risk phrase.`
              : "",
      },
    );
    const mapCount = els.huntMapList ? els.huntMapList.children.length : 0;
    setStatus(
      `Hunt · ${mapCount} map(s) · ${results.length} article(s) of ${state.lastTotalIndexed} indexed`,
    );
    syncHuntUsecases();
    renderFilterCrumbs();
  }

  async function reapplyActiveFilters() {
    await ensureItmCatalog(true);
    if (state.selectedTechniqueId) {
      await selectTechnique(state.selectedTechniqueId);
      return;
    }
    if (state.selectedDetectionId) {
      await selectDetection(state.selectedDetectionId);
      return;
    }
    if (state.selectedPreventionId) {
      await selectPrevention(state.selectedPreventionId);
      return;
    }
    if (state.searchMode && state.lastHuntQuery) {
      await runSearch(state.lastHuntQuery);
      return;
    }
    renderMatrixBrowse();
    await reloadStreamOrSearch();
  }

  if (els.alignFilters) {
    els.alignFilters.addEventListener("click", (event) => {
      const btn = event.target.closest(".pill[data-alignment]");
      if (!btn) return;
      state.itmAlignment = btn.dataset.alignment || "insider";
      setActiveScopePill(state.itmAlignment);
      updateRefineSummary();
      reapplyActiveFilters().catch((err) => setStatus(`Load failed: ${err.message}`));
    });
  }

  if (els.channelFilters) {
    els.channelFilters.addEventListener("click", (event) => {
      const btn = event.target.closest(".pill[data-channel]");
      if (!btn) return;
      state.channel = btn.dataset.channel || "all";
      setActiveChannelPill(state.channel);
      updateRefineSummary();
      reapplyActiveFilters().catch((err) => setStatus(`Load failed: ${err.message}`));
    });
  }

  if (els.insiderTypeFilters) {
    els.insiderTypeFilters.addEventListener("click", (event) => {
      const btn = event.target.closest(".pill[data-insider-type]");
      if (!btn) return;
      state.insiderType = btn.dataset.insiderType || "all";
      setActiveInsiderTypePill(state.insiderType);
      updateRefineSummary();
      reapplyActiveFilters().catch((err) => setStatus(`Load failed: ${err.message}`));
    });
  }

  if (els.refreshStream) {
    els.refreshStream.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      refreshStream().catch((err) => setStatus(`Refresh failed: ${err.message}`));
    });
  }

  function runCopyExport(mode) {
    const article = selectedArticle();
    if (!article) {
      setStatus("Select an article first");
      return;
    }
    if (mode === "plaintext") {
      const text = plaintextExport(article);
      copyText(
        text,
        text
          ? `Copied ${composeOperatorTerms(article).length} operator term(s)`
          : "No operator terms",
      );
    } else if (mode === "json") {
      copyText(jsonExport(article), "Copied JSON payload");
    } else if (mode === "llm") {
      copyText(llmExport(article), "Copied LLM prompt");
    }
  }

  if (els.copyPlaintext) {
    els.copyPlaintext.addEventListener("click", () => runCopyExport("plaintext"));
  }

  if (els.boardToggle) {
    els.boardToggle.addEventListener("click", () => {
      const article = selectedArticle();
      if (!article) {
        setStatus("Select an article first");
        return;
      }
      if (articleOnBoard(article.link)) removeFromBoard(article.link);
      else addToBoard(article, { focusWorkbench: true });
    });
  }

  if (els.boardExtract) {
    els.boardExtract.addEventListener("click", () => {
      runBoardExtract().catch((err) => setStatus(`Extract failed: ${err.message}`));
    });
  }

  if (els.boardClear) {
    els.boardClear.addEventListener("click", () => clearBoard());
  }

  if (els.boardShare) {
    els.boardShare.addEventListener("click", () => {
      shareBoardLink().catch((err) => setStatus(`Share failed: ${err.message}`));
    });
  }

  if (els.boardExport) {
    els.boardExport.addEventListener("click", () => exportBoardFile());
  }

  if (els.boardImport && els.boardImportFile) {
    els.boardImport.addEventListener("click", () => els.boardImportFile.click());
    els.boardImportFile.addEventListener("change", async () => {
      const file = els.boardImportFile.files && els.boardImportFile.files[0];
      els.boardImportFile.value = "";
      if (!file) return;
      try {
        importBoardData(JSON.parse(await file.text()));
      } catch (err) {
        setStatus(`Board import failed: ${err.message}`);
      }
    });
  }

  if (els.boardCopyBrief) {
    els.boardCopyBrief.addEventListener("click", () => {
      const text = agentBriefPlaintext(boardEntries());
      copyText(text, text ? "Copied LLM brief" : "Add articles to the board first");
    });
  }

  if (els.copyTtpReport) {
    els.copyTtpReport.addEventListener("click", () => {
      const text = ttpReportPlaintext(state.lastTtpReport);
      copyText(text, text ? "Copied hunt report" : "Run Extract TTPs first");
    });
  }

  if (els.copyTtpLlm) {
    els.copyTtpLlm.addEventListener("click", () => {
      const text = ttpReportLlmQuery(state.lastTtpReport);
      copyText(text, text ? "Copied LLM query" : "Run Extract TTPs first");
    });
  }

  if (els.matrixModeTabs) {
    els.matrixModeTabs.addEventListener("click", (event) => {
      const btn = event.target.closest("[data-matrix-mode]");
      if (!btn) return;
      state.matrixMode = btn.dataset.matrixMode || "techniques";
      renderMatrixBrowse();
      setStatus(`Matrix · ${state.matrixMode}`);
    });
  }

  if (els.matrixLatest) {
    els.matrixLatest.addEventListener("click", () => {
      showLatest().catch((err) => setStatus(`Load failed: ${err.message}`));
    });
  }

  if (els.dossierBack) {
    els.dossierBack.addEventListener("click", () => {
      showLatest().catch((err) => setStatus(`Load failed: ${err.message}`));
    });
  }

  if (els.mobileTabs) {
    els.mobileTabs.addEventListener("click", (event) => {
      const btn = event.target.closest(".mobile-tab[data-pane]");
      if (!btn) return;
      setActivePane(btn.dataset.pane || "articles");
    });
  }

  if (typeof MOBILE_MQ.addEventListener === "function") {
    MOBILE_MQ.addEventListener("change", () => syncPaneForViewport());
    WIDE_MQ.addEventListener("change", () => syncPaneForViewport());
  } else if (typeof MOBILE_MQ.addListener === "function") {
    MOBILE_MQ.addListener(() => syncPaneForViewport());
    WIDE_MQ.addListener(() => syncPaneForViewport());
  }

  if (els.sourceSelect) {
    els.sourceSelect.addEventListener("change", () => {
      state.sourceId = els.sourceSelect.value || "";
      updateRefineSummary();
      reapplyActiveFilters().catch((err) => setStatus(`Load failed: ${err.message}`));
    });
  }

  if (els.matrixQ) {
    els.matrixQ.addEventListener("input", () => {
      state.matrixQuery = els.matrixQ.value || "";
      renderMatrixBrowse();
    });
  }

  els.searchForm.addEventListener("submit", (event) => {
    event.preventDefault();
    const query = els.q.value.trim();
    if (!query) return;
    runSearch(query).catch((err) => setStatus(`Search failed: ${err.message}`));
    if (isMobileLayout()) setActivePane("articles");
  });

  els.clearSearch.addEventListener("click", () => {
    els.q.value = "";
    els.clearSearch.hidden = true;
    state.searchMode = false;
    showLatest().catch((err) => setStatus(`Load failed: ${err.message}`));
  });

  function socialSourceRow(info, { subscribed }) {
    const li = document.createElement("li");
    li.className = "social-source-item";
    const main = document.createElement("span");
    main.className = "social-source-name";
    main.textContent = info.name;
    if (info.use_cases && info.use_cases.length) {
      main.title = info.use_cases
        .map((id) => USE_CASE_LABELS[id] || id)
        .join(", ");
    }
    const count = document.createElement("span");
    count.className = "meta";
    count.textContent = info.article_count ? ` (${info.article_count})` : "";
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "ghost social-source-btn";
    btn.textContent = subscribed ? "Remove" : "Add";
    btn.addEventListener("click", () => {
      const action = subscribed
        ? api(
            `/social/subscriptions/${encodeURIComponent(info.platform)}/${encodeURIComponent(info.id)}`,
            {},
            { method: "DELETE" },
          )
        : api(
            "/social/subscriptions",
            {},
            {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ platform: info.platform, id: info.id }),
            },
          );
      action
        .then(() => loadSocialCatalog())
        .then(() => loadSources())
        .catch((err) => setStatus(`Social update failed: ${err.message}`));
    });
    li.append(main, count, btn);
    return li;
  }

  async function loadSocialCatalog() {
    if (!els.socialManager) return;
    let catalog;
    try {
      catalog = await api("/social/catalog");
    } catch (err) {
      // Demo mode / older API without social endpoints: hide the panel.
      els.socialManager.hidden = true;
      console.warn("Social catalog unavailable", err);
      return;
    }
    els.socialManager.hidden = false;
    const subscriptions = (catalog && catalog.subscriptions) || [];
    const suggestions = ((catalog && catalog.suggestions) || []).filter(
      (info) => !info.subscribed,
    );
    if (els.socialSubscribed) {
      els.socialSubscribed.innerHTML = "";
      subscriptions.forEach((info) => {
        els.socialSubscribed.appendChild(socialSourceRow(info, { subscribed: true }));
      });
    }
    if (els.socialSubscribedEmpty) {
      els.socialSubscribedEmpty.hidden = subscriptions.length > 0;
    }
    if (els.socialSuggested) {
      els.socialSuggested.innerHTML = "";
      suggestions.forEach((info) => {
        els.socialSuggested.appendChild(socialSourceRow(info, { subscribed: false }));
      });
    }
  }

  if (els.socialAddForm) {
    els.socialAddForm.addEventListener("submit", (event) => {
      event.preventDefault();
      const platform = (els.socialAddPlatform && els.socialAddPlatform.value) || "reddit";
      const handle = ((els.socialAddHandle && els.socialAddHandle.value) || "").trim();
      if (!handle) return;
      api(
        "/social/subscriptions",
        {},
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ platform, id: handle }),
        },
      )
        .then(() => {
          if (els.socialAddHandle) els.socialAddHandle.value = "";
          return loadSocialCatalog();
        })
        .then(() => loadSources())
        .catch((err) => setStatus(`Social add failed: ${err.message}`));
    });
  }

  async function boot() {
    try {
      initRefinePanel();
      syncPaneForViewport();
      loadDismissed();
      loadExtractionBoard();
      renderExtractionBoard();
      syncBoardToggle();
      renderHuntUsecases();

      // Public UI defaults to live API; if Cloud Run is not up yet, fall back to
      // the static web/demo snapshot so Workbench / Extract / Copy still work.
      if (!demoMode) {
        try {
          await api("/health", {}, { timeoutMs: 4000 });
        } catch (liveErr) {
          if (!window.InsiderIntelDemo) throw liveErr;
          console.warn("Live API unreachable — falling back to static demo", liveErr);
          demoMode = true;
          window.INSIDER_INTEL_DEMO = true;
          setStatus("Live API offline — using static demo");
        }
      }

      if (demoMode && window.InsiderIntelDemo) {
        await window.InsiderIntelDemo.ready;
      }
      const health = await api("/health");
      const demoNote = health.demo || demoMode ? " · static demo" : "";
      setStatus(`API ok · ${health.indexed_articles} indexed${demoNote}`);
      state.lastTotalIndexed = health.indexed_articles || 0;
      state.dataState = {
        mode: health.demo || demoMode ? "demo" : "live",
        indexed: health.indexed_articles || 0,
        generatedAt: health.generated_at || null,
      };
      renderDataState();
      await ensureItmCatalog();
      renderMatrixBrowse();
      await loadSources();
      loadSocialCatalog().catch((err) => console.warn("Social catalog failed", err));
      const route = parseRoute();
      if (route.view === "technique" && route.id) {
        await showDossier(route.id);
      } else if (route.view === "board") {
        await loadArticles();
        await importBoardFromRoute(route);
      } else {
        await loadArticles();
      }
    } catch (err) {
      setStatus(
        demoMode || window.INSIDER_INTEL_DEMO
          ? `Demo failed to load (missing web/demo?). ${err.message}`
          : `Cannot reach API at ${apiBase}. Try ?demo=1 for the static snapshot.`,
      );
      renderArticles([], "Latest");
      console.error(err);
    }
  }

  boot();
})();
