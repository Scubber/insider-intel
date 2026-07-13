/* Search-ready hunt query templates — pure client-side string building so the
 * output is identical in live-API and demo modes. Templates stay conservative:
 * term lists over invented joins, one block per target product. */
(() => {
  const MAX_TERMS = 15;

  function cleanTerms(terms) {
    const seen = new Set();
    const out = [];
    (terms || []).forEach((raw) => {
      const term = String(raw || "").trim();
      if (term.length < 2) return;
      const key = term.toLowerCase();
      if (seen.has(key)) return;
      seen.add(key);
      out.push(term);
    });
    return out;
  }

  function capTerms(terms) {
    const cleaned = cleanTerms(terms);
    return {
      used: cleaned.slice(0, MAX_TERMS),
      dropped: Math.max(0, cleaned.length - MAX_TERMS),
    };
  }

  function escapeKql(term) {
    return String(term).replace(/\\/g, "\\\\").replace(/"/g, '\\"');
  }

  function escapeSpl(term) {
    return String(term).replace(/\\/g, "\\\\").replace(/"/g, '\\"');
  }

  function escapeRegex(term) {
    return String(term).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  }

  function kqlQuotedList(terms) {
    return terms.map((t) => `"${escapeKql(t)}"`).join(", ");
  }

  function droppedNote(dropped) {
    return dropped > 0 ? ` // +${dropped} more term(s) trimmed — narrow first` : "";
  }

  function defenderEmail(terms) {
    const { used, dropped } = capTerms(terms);
    if (!used.length) return null;
    return {
      id: "kql-defender-email",
      stack: "Microsoft Defender (advanced hunting)",
      lang: "KQL",
      label: "Defender — email subjects/URLs",
      query: [
        `EmailEvents${droppedNote(dropped)}`,
        "| where Timestamp > ago(30d)",
        `| where Subject has_any (${kqlQuotedList(used)})`,
        "| project Timestamp, SenderFromAddress, RecipientEmailAddress, Subject, DeliveryAction",
        "| order by Timestamp desc",
      ].join("\n"),
    };
  }

  function sentinelActivity(terms) {
    const { used, dropped } = capTerms(terms);
    if (!used.length) return null;
    return {
      id: "kql-sentinel-office",
      stack: "Microsoft Sentinel",
      lang: "KQL",
      label: "Sentinel — Office activity sweep",
      query: [
        `let hunt_terms = dynamic([${kqlQuotedList(used)}]);${droppedNote(dropped)}`,
        "OfficeActivity",
        "| where TimeGenerated > ago(30d)",
        "| where * has_any (hunt_terms)",
        "| project TimeGenerated, UserId, Operation, OfficeWorkload, SourceFileName",
        "| order by TimeGenerated desc",
      ].join("\n"),
    };
  }

  function purviewCondition(terms) {
    const { used, dropped } = capTerms(terms);
    if (!used.length) return null;
    const body = used.map((t) => `"${escapeKql(t)}"`).join(" OR ");
    return {
      id: "purview-ediscovery",
      stack: "Microsoft Purview / eDiscovery",
      lang: "KQL condition",
      label: "Purview — email + Teams content search",
      query: `(${body}) AND (kind:email OR kind:microsoftteams)${droppedNote(dropped)}`,
    };
  }

  function splunkSweep(terms) {
    const { used, dropped } = capTerms(terms);
    if (!used.length) return null;
    const body = used.map((t) => `"${escapeSpl(t)}"`).join(" OR ");
    return {
      id: "spl-sweep",
      stack: "Splunk",
      lang: "SPL",
      label: "Splunk — cross-index term sweep",
      query: [
        `index=* earliest=-30d (${body})${droppedNote(dropped)}`,
        "| stats count by user, sourcetype, index",
        "| sort -count",
      ].join("\n"),
    };
  }

  function messageTrace(terms) {
    const { used, dropped } = capTerms(terms);
    if (!used.length) return null;
    const pattern = used.map(escapeRegex).join("|");
    return {
      id: "exo-message-trace",
      stack: "Exchange Online (PowerShell)",
      lang: "PowerShell",
      label: "Exchange — message trace by subject",
      query: [
        `# Requires ExchangeOnlineManagement; trace window max 10 days${droppedNote(dropped)}`,
        "Get-MessageTrace -StartDate (Get-Date).AddDays(-10) -EndDate (Get-Date) |",
        `  Where-Object { $_.Subject -match "${pattern.replace(/"/g, '`"')}" } |`,
        "  Select-Object Received, SenderAddress, RecipientAddress, Subject",
      ].join("\n"),
    };
  }

  /**
   * Build paste-and-run query blocks.
   * terms: general seed terms (dossier aliases or report seeds).
   * emailCues / chatCues / networkCues: channel-specific phrasing when
   * available (hunt report); fall back to the general terms.
   */
  function buildQueries({ terms = [], emailCues = [], chatCues = [], networkCues = [] } = {}) {
    const base = cleanTerms(terms);
    const email = cleanTerms([...emailCues, ...base]);
    const content = cleanTerms([...emailCues, ...chatCues, ...base]);
    const network = cleanTerms([...networkCues, ...base]);
    return [
      defenderEmail(email),
      sentinelActivity(content),
      purviewCondition(content),
      splunkSweep(network),
      messageTrace(email),
    ].filter(Boolean);
  }

  window.InsiderIntelTemplates = { buildQueries };
})();
