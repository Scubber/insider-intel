/* Matrix, technique dossier, workbench, evidence, about. */
const { Panel: P2, Chip: Chip2, ItmChip: ItmChip2, Pill: Pill2, ActionButton: AB2, CopyButton: CB2, TechniqueSection, FactList: FL2 } = window.DossierUI;
const D2 = window.II_DATA;
const THEME_ORDER = ["Motive", "Means", "Preparation", "Infringement", "Anti-Forensics"];

function casesFor(techId) { return D2.cases.filter((c) => c.itm.includes(techId)); }

function MatrixScreen({ ctx }) {
  const byTheme = {};
  Object.entries(D2.techniques).forEach(([id, t]) => { (byTheme[t.theme] = byTheme[t.theme] || []).push([id, t]); });
  return (
    <div className="ii-single-col">
      <p className="ii-screen-lede">TECHNIQUE ROLLUP — corpus-observed subset of the Insider Threat Matrix™. Each dossier carries detections, preventions, and hunt logic distilled from its cases.</p>
      <div className="ii-matrix-cols">
        {THEME_ORDER.filter((th) => byTheme[th]).map((th) => (
          <div key={th} className="ii-matrix-col">
            <p className="ii-matrix-theme">{th.toUpperCase()}</p>
            {byTheme[th].sort((a, b) => casesFor(b[0]).length - casesFor(a[0]).length).map(([id, t]) => {
              const n = casesFor(id).length;
              return (
                <button key={id} type="button" className="ii-matrix-cell" onClick={() => ctx.openTech(id)}>
                  <span className="ii-cell-head"><span className="ii-rail-id">{id}</span>{t.corr && <span className="ii-corr" title="Detection corroborated by case evidence">✓ CORR</span>}</span>
                  <b>{t.name}</b>
                  <em>{n} CASE{n === 1 ? "" : "S"} · {t.detections.length} DT · {t.preventions.length} PV</em>
                </button>
              );
            })}
          </div>
        ))}
      </div>
      <p className="ii-rail-note">ITM™ © Forscie Limited — not affiliated. Techniques without corpus cases are omitted from this prototype.</p>
    </div>
  );
}

function TechniqueScreen({ ctx, techId }) {
  const t = D2.techniques[techId];
  if (!t) return <p className="ii-empty">TECHNIQUE NOT FOUND.</p>;
  const related = casesFor(techId);
  return (
    <div className="ii-detail-grid">
      <div>
        <button type="button" className="ii-back" onClick={() => ctx.nav("matrix")}>← MATRIX</button>
        <div className="ii-tech-head">
          <h2 className="ii-tech-title"><span className="ii-rail-id ii-rail-id-lg">{techId}</span>{t.name}</h2>
          <p className="ii-tech-meta">{t.theme.toUpperCase()} · {related.length} CORPUS CASES · {t.corr ? "DETECTION BACKED BY REAL CASE EVIDENCE ✓" : "NOT YET SEEN IN COURT RECORD"}</p>
          <p className="ii-tech-desc">{t.desc}</p>
        </div>
        <P2 title="HOW TO HUNT THIS — DISTILLED FROM THE CASES">
          <TechniqueSection id={techId} description={t.desc} cases={related.map((c) => ({ title: `${c.title} — ${c.kind} ${c.id}`, bullets: [c.note] }))} />
          <ul className="ii-hunt-list">{t.hunts.map((h) => <li key={h}>{h}</li>)}</ul>
          <div className="ii-action-row">
            <CB2 primary onClick={() => navigator.clipboard && navigator.clipboard.writeText(t.hunts.join("\n"))}>COPY HUNT LOGIC</CB2>
            <CB2 onClick={() => {}}>COPY LLM PROMPT</CB2>
          </div>
        </P2>
        <P2 title={`RELATED CASES (${related.length})`}>
          <div className="ii-case-stack">{related.map((c) => <IICaseCard key={c.id} c={c} ctx={ctx} />)}</div>
        </P2>
      </div>
      <aside className="ii-rail">
        <P2 title="DETECTIONS">
          {t.detections.map(([id, label]) => <p key={id} className="ii-ctl-row"><span className="ii-rail-id">{id}</span>{label}</p>)}
        </P2>
        <P2 title="PREVENTIONS">
          {t.preventions.map(([id, label]) => <p key={id} className="ii-ctl-row"><span className="ii-rail-id">{id}</span>{label}</p>)}
        </P2>
        <P2 title="REFERENCE">
          <div className="ii-action-col"><CB2 onClick={() => {}}>OPEN IN ITM™ ↗</CB2></div>
        </P2>
      </aside>
    </div>
  );
}

function WorkbenchScreen({ ctx }) {
  const board = D2.cases.filter((c) => ctx.flagged.includes(c.id));
  const techMap = {};
  board.forEach((c) => c.itm.forEach((t) => { (techMap[t] = techMap[t] || []).push(c); }));
  return (
    <div className="ii-detail-grid">
      <div>
        <div className="ii-wb-head">
          <h2 className="ii-wb-title">MODUS OPERANDI</h2>
          <p className="ii-tech-meta">{board.length ? `ASSEMBLED FROM ${board.length} FLAGGED CASE${board.length === 1 ? "" : "S"} · STORED FORENSICS ONLY · NO MODEL RUNS AT READ TIME` : "AWAITING FLAGGED CASES"}</p>
        </div>
        {board.length ? (
          <>
            <p className="ii-wb-summary">Across the flagged set, insiders {Object.keys(techMap).length > 1 ? "combined " + Object.keys(techMap).length + " distinct techniques" : "relied on a single technique"} — most acting during employment or the notice period, with detection arriving through central audit trails rather than endpoint controls.</p>
            {Object.entries(techMap).map(([tid, cs]) => (
              <TechniqueSection key={tid} id={tid} description={D2.techniques[tid].name + " — " + D2.techniques[tid].desc} cases={cs.map((c) => ({ title: `${c.title} — ${c.kind} ${c.id}`, bullets: [c.note, "Detected via: " + ((c.facts.find((f) => f[0] === "DETECTED VIA") || [])[1] || "not established in record")] }))} />
            ))}
            <div className="ii-action-row">
              <CB2 primary onClick={() => {}}>COPY REPORT</CB2>
              <CB2 onClick={() => {}}>EXPORT NDJSON</CB2>
              <CB2 onClick={() => {}}>COPY LLM RESEARCH BRIEF</CB2>
            </div>
          </>
        ) : (
          <p className="ii-empty">BOARD EMPTY. FLAG CASES FROM THE STREAM (+ FLAG, OR x) TO ASSEMBLE A REPORT.</p>
        )}
      </div>
      <aside className="ii-rail">
        <P2 title={`EVIDENCE BOARD (${board.length})`}>
          {board.map((c) => (
            <div key={c.id} className="ii-board-row">
              <button type="button" className="ii-board-title" onClick={() => ctx.openCase(c.id)}>{c.title}</button>
              <span className="ii-board-meta">{c.kind} {c.id} · SIG {c.sig}</span>
              <AB2 onClick={() => ctx.toggleFlag(c.id)}>REMOVE ✕</AB2>
            </div>
          ))}
          {!board.length && <p className="ii-rail-note">Nothing flagged.</p>}
        </P2>
        <P2 title="BOARD ACTIONS">
          <div className="ii-action-col">
            <CB2 onClick={() => {}}>SHARE LINK</CB2>
            <CB2 onClick={() => {}}>EXPORT JSON</CB2>
            <CB2 onClick={() => {}}>IMPORT JSON</CB2>
          </div>
          <p className="ii-rail-note">Boards encode in the URL — no accounts, nothing stored server-side.</p>
        </P2>
      </aside>
    </div>
  );
}

function EvidenceScreen({ ctx }) {
  const maxFn = Math.max(...D2.actors.fn.map((x) => x[1]));
  const maxSt = Math.max(...D2.actors.state.map((x) => x[1]));
  return (
    <div className="ii-single-col">
      <p className="ii-screen-lede">THE EVIDENCE LEDGER — recomputed across every method-bearing case. Cases confirmed in court are always counted separately from ones that are only alleged; percentages suppress below a small-n floor. Read every number as “of litigated insider cases.”</p>
      <div className="ii-ev-grid">
        <P2 title="FINDINGS — VERSIONED · OPERATOR-APPROVED BY MERGE">
          {D2.findings.map((f) => (
            <div key={f.id} className="ii-finding">
              <p className="ii-finding-claim"><span className="ii-rail-id">{f.id}</span>{f.claim}</p>
              <FL2 items={[{ label: "CAVEAT", value: f.caveat }, { label: "PROGRAM REC", value: f.rec }]} />
            </div>
          ))}
        </P2>
        <P2 title="WHERE THE EVIDENCE LIVES">
          <div className="ii-rail-list">
            {D2.trail.map(([label, all, adj]) => (
              <div key={label} className="ii-rail-row ii-rail-row-static">
                <span className="ii-rail-name">{label}</span>
                <span className="ii-rail-bar"><i style={{ width: all + "%" }}></i><i className="ii-bar-adj" style={{ width: adj + "%" }}></i></span>
                <span className="ii-rail-n">{all}% / {adj}%</span>
              </div>
            ))}
          </div>
          <p className="ii-rail-note">Light = share of all method-bearing cases · dark = confirmed in court.</p>
        </P2>
        <P2 title="ACTOR PROFILE — ROLES, NEVER INDIVIDUALS">
          <div className="ii-who-grid">
            <div><p className="ii-axis">FUNCTION</p>{D2.actors.fn.map(([label, n]) => <div key={label} className="ii-rail-row ii-rail-row-static"><span className="ii-rail-name">{label}</span><span className="ii-rail-bar"><i style={{ width: (n / maxFn * 100) + "%" }}></i></span><span className="ii-rail-n">{n}%</span></div>)}</div>
            <div><p className="ii-axis">EMPLOYMENT STATE AT THE ACT</p>{D2.actors.state.map(([label, n]) => <div key={label} className="ii-rail-row ii-rail-row-static"><span className="ii-rail-name">{label}</span><span className="ii-rail-bar"><i style={{ width: (n / maxSt * 100) + "%" }}></i></span><span className="ii-rail-n">{n}%</span></div>)}</div>
          </div>
        </P2>
        <P2 title="LIMITATIONS — READ BEFORE CITING">
          <p className="ii-limits"><b>Selection bias:</b> court data measures insiders who were caught and litigated, not insider behavior at large. <b>Small samples:</b> percentages suppressed below a case floor; counts always shown. <b>Attribution scope:</b> technique ids are case-level. <b>Collection bias:</b> the corpus reflects our own query lexicon and sweep history. Records are machine-extracted from filings and normalized; every number is reproducible from stored forensic records.</p>
        </P2>
      </div>
    </div>
  );
}

function AboutScreen({ ctx }) {
  return (
    <div className="ii-single-col ii-about">
      <button type="button" className="ii-back" onClick={() => ctx.nav("stream")}>← STREAM</button>
      <h2 className="ii-tech-title">METHODOLOGY & COLOPHON</h2>
      <p>insider-intel is an open OSINT research instrument for discovering novel insider techniques — tradecraft that shows up in real cases before it shows up in any framework. It ingests litigated insider cases, insider-relevant news, first-person social confessions, and long-form publications; forensically enriches each case at ingest; and aggregates the corpus into evidence about how insider incidents actually happen and what record classes actually prove them.</p>
      <FL2 items={[
        { label: "SIG", value: "Insider-confidence score, 0–100, assigned at ingest. The stream floor defaults to 30." },
        { label: "PROOF STANDARD", value: "Every count separates CONFIRMED IN COURT (a judge ruled it happened, or the insider admitted it) from ALLEGED (claimed in a filing, not yet decided) from REPORTED (press or social only). They are never conflated." },
        { label: "READ PATH", value: "No model runs at read time. Everything on screen is a projection of stored, append-only forensic records." },
        { label: "PRIVACY", value: "Roles, never individuals — no persona graphs, no entity resolution across cases." },
        { label: "EDITORIAL", value: "Findings publish by merge to main. The GitOps trail is the editorial record." },
        { label: "ATTRIBUTION", value: "Insider Threat Matrix™ © Forscie Limited. This project is not affiliated with or endorsed by Forscie." }
      ]} />
    </div>
  );
}

Object.assign(window, { MatrixScreen, TechniqueScreen, WorkbenchScreen, EvidenceScreen, AboutScreen });
