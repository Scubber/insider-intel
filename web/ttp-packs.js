/* Curated extraction-board TTP seed packs — single source of truth shared by
 * app.js (the shipped site) and the standalone preview's offline responder,
 * so the two never drift.
 * A pack is selected when the board's ITM ids, hunt query, or article text
 * match its anchors/aliases; IF038 is the default when nothing matches. */
(() => {
  const IF038_SEEDS = [
    {
      id: "TTP-OE-01",
      behavior: "Undisclosed second full-time remote job (dual employment / overemployment).",
      email: [
        "personal-domain mail during work hours",
        "Job B recruiter/HR threads",
        "personal calendar invites for Job B standups",
      ],
      chat: ["second Slack/Teams identity", "J2 / OE / overemployed language", "status always Busy/BRB"],
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
      chat: ["screenshots of internal tools", "my other company", "recruiting coworkers"],
      network: ["large personal-cloud uploads", "USB/email exfil near resignation", "repos unused in day job"],
      human: ["undisclosed advisory/contractor role", "COI form none", "resignation timed with competitor start"],
      seeds: ["competitor", "side project", "advisory", "consulting agreement", "DTSA", "trade secret", "customer list"],
    },
    {
      id: "TTP-OE-03",
      behavior: "Using Employer A time/tools for Employer B.",
      email: ["drafts to Job B from corp mailbox", "vague calendar blocks with no corp attendees"],
      chat: ["Job B tickets pasted into corp chat", "second browser profile language"],
      network: ["Job B IdP on corp device", "RDP/VDI to personal systems", "clipboard/file activity to personal cloud"],
      human: ["timekeeping anomalies", "always in meetings without corp artifacts", "PIP for availability"],
      seeds: ["personal laptop", "my other job", "client call"],
    },
    {
      id: "TTP-OE-04",
      behavior: "Identity split — personal stack for Job B, corp stack for Job A.",
      email: ["auto-forward corp to personal", "Job B never on corp systems"],
      chat: ["text me on my personal", "Signal/WhatsApp for work topics"],
      network: ["MDM gaps", "personal hotspot only", "corp VPN idle while claiming hours"],
      human: ["unreachable on corp mobile", "refuses MDM on personal devices used for work"],
      seeds: ["personal phone", "text me", "Signal", "WhatsApp", "forward to Gmail"],
    },
    {
      id: "TTP-OE-05",
      behavior: "False or incomplete outside-employment / COI disclosure.",
      email: ["outside employment policy signature threads unanswered", "policy reminders ignored"],
      chat: ["don't tell HR", "policy screenshot shares"],
      network: ["pair with HRIS — low network signal alone"],
      human: ["form answers vs LinkedIn/tax/benefits", "AP payments to employee LLC", "1099s"],
      seeds: ["outside employment policy", "conflict of interest form", "disclosure form", "moonlighting policy"],
    },
  ];

  const ATTRIBUTION_SEEDS = [
    {
      id: "TTP-ATTR-01",
      behavior:
        "Persistent device telemetry (GDID / device fingerprint) survives VPNs and reinstalls, linking one machine across personas.",
      email: [
        "same device across personal (Apple/Snapchat/Facebook) and operational mail",
        "account-recovery mail tied to one device",
      ],
      chat: ["operational and personal chat identities on one device fingerprint"],
      network: [
        "same device identifier across disparate geo IPs",
        "VPN + residential proxy + ngrok tunnel patterns",
        "telemetry correlation across sessions",
        "IP geolocation mismatch vs claimed location",
      ],
      human: [
        "claimed location vs device geolocation",
        "cross-reference personal gaming/social accounts to the device",
      ],
      seeds: [
        "GDID",
        "global device identifier",
        "device fingerprint",
        "telemetry identifier",
        "deanonymization",
        "ngrok",
        "residential proxy",
      ],
    },
    {
      id: "TTP-ATTR-02",
      behavior: "Identity linkage via reused infrastructure, personas, and accounts.",
      email: ["reused registration email across personas", "recovery phone/email overlap"],
      chat: ["shared handle reuse across platforms"],
      network: ["reused VPN/proxy endpoints across incidents", "ASN / hosting reuse"],
      human: ["OSINT link of personas to a real identity", "breach-data correlation of email/phone"],
      seeds: ["persona reuse", "infrastructure reuse", "account linking", "sim swap"],
    },
    {
      id: "TTP-ATTR-03",
      behavior:
        "Help-desk social engineering to seize accounts (Scattered-Spider hallmark) — the intrusion these forensics attribute.",
      email: ["password-reset and MFA-reset request threads", "IT help-desk ticket for account recovery"],
      chat: ["vishing pretext scripts", "impersonating employee to service desk"],
      network: ["MFA fatigue / push-bombing patterns", "new device enrollment right after reset"],
      human: ["help-desk verification bypass", "employee impersonation to reset MFA"],
      seeds: ["help desk social engineering", "MFA reset", "vishing", "push bombing", "sim swap"],
    },
  ];

  const PACKS = [
    {
      id: "IF038",
      label: "IF038 overemployment",
      anchorIds: ["IF038"],
      aliasHints: [
        "overemployment",
        "over-employment",
        "moonlighting",
        "dual employment",
        "concurrent employment",
        "outside employment",
        "second job",
        "j2",
      ],
      seeds: IF038_SEEDS,
    },
    {
      id: "AF029-ATTR",
      label: "attribution forensics",
      anchorIds: ["AF029", "PR022", "PR027"],
      aliasHints: [
        "gdid",
        "global device identifier",
        "device fingerprint",
        "device identifier",
        "digital fingerprint",
        "telemetry identifier",
        "deanonymization",
        "deanonymized",
        "scattered spider",
        "sim swap",
        "ngrok",
        "residential proxy",
        "help desk social engineering",
        "vishing",
      ],
      seeds: ATTRIBUTION_SEEDS,
    },
  ];

  /**
   * selectPacks({ itmIds, huntQuery, texts }) → { packs, matched }
   * Returns matched packs (or [IF038] default). `matched` is false when the
   * default was used as a fallback.
   */
  function selectPacks({ itmIds = [], huntQuery = "", texts = [] } = {}) {
    const ids = itmIds.map((id) => String(id).toUpperCase());
    const hay = [huntQuery, ...texts].map((t) => String(t || "").toLowerCase());
    const packHit = (pack) => {
      if (pack.anchorIds.some((a) => ids.includes(a))) return true;
      return pack.aliasHints.some((h) => h && hay.some((t) => t.includes(h)));
    };
    const matched = PACKS.filter(packHit);
    return { packs: matched.length ? matched : [PACKS[0]], matched: matched.length > 0 };
  }

  window.InsiderIntelPacks = { PACKS, DEFAULT: PACKS[0], selectPacks };
})();
