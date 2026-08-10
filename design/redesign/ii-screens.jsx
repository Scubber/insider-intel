/* Stream + case dossier screens. */
const { Panel, CaseCard, FactList, Chip, ItmChip, Pill, ActionButton, CopyButton } = window.DossierUI;
const DATA = window.II_DATA;

function caseMeta(c) {
  const bits = [c.source];
  if (c.docket) bits.push(c.docket);
  bits.push(`FILED ${c.filed}`, `RETRIEVED ${c.retrieved.split(" ")[0]}`, `SIG ${c.sig}`, c.strength);
  return bits.join(" · ");
}

function IICaseCard({ c, ctx, full }) {
  const flagged = ctx.flagged.includes(c.id);
  return (
    <CaseCard
      tab={`${c.kind} ${c.id}`}
      title={c.title}
      meta={caseMeta(c)}
      note={c.note}
      facts={full ? c.facts.map(([label, value]) => ({ label, value })) : undefined}
      footer={<>{c.itm.map((t) => <ItmChip key={t} id={t} title={DATA.techniques[t] ? DATA.techniques[t].name : t} onClick={() => ctx.openTech(t)} />)}{(full ? c.terms : c.terms.slice(0, 2)).map((t) => <Chip key={t}>{t}</Chip>)}</>}
      actions={<>
        <ActionButton active={flagged} onClick={() => ctx.toggleFlag(c.id)}>{flagged ? "✓ FLAGGED" : "+ FLAG"}</ActionButton>
        {!full && <ActionButton onClick={() => ctx.openCase(c.id)}>DOSSIER →</ActionButton>}
        <ActionButton onClick={() => {}}>SOURCE ↗</ActionButton>
      </>}
    />
  );
}
const CHANNELS = [["all", "ALL"], ["filings", "FILINGS"], ["news", "NEWS"], ["social", "SOCIAL"]];

function StreamScreen({ ctx }) {
  const [channel, setChannel] = React.useState("all");
  const [floor, setFloor] = React.useState(30);
  const [q, setQ] = React.useState("");
  const cases = DATA.cases.filter((c) =>
    (channel === "all" || c.channel === channel) && c.sig >= floor &&
    (!q || (c.title + " " + c.note + " " + c.terms.join(" ")).toLowerCase().includes(q.toLowerCase())));
  const techCounts = {};
  cases.forEach((c) => c.itm.forEach((t) => { techCounts[t] = (techCounts[t] || 0) + 1; }));
  const topTech = Object.entries(techCounts).sort((a, b) => b[1] - a[1]);
  return (
    <div className="ii-stream-grid">
      <div className="ii-stream-col">
        <div className="ii-queryline">
          <span className="ii-query-icon" aria-hidden="true"><svg viewBox="0 0 16 16" width="14" height="14"><circle cx="7" cy="7" r="4.5" fill="none" stroke="currentColor" strokeWidth="1.5"></circle><line x1="10.5" y1="10.5" x2="14" y2="14" stroke="currentColor" strokeWidth="1.5"></line></svg></span>
          <input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search cases — data theft, shared logins, second job, sabotage" aria-label="Search corpus" />
          {q && <button type="button" className="ii-query-clear" onClick={() => setQ("")}>CLEAR</button>}
        </div>
        <div className="ii-filterline">
          <span className="ii-filter-label">CHANNEL</span>
          {CHANNELS.map(([k, label]) => <Pill key={k} active={channel === k} onClick={() => setChannel(k)}>{label}</Pill>)}
          <span className="ii-filter-label ii-filter-gap">SIG ≥ {floor}</span>
          <input type="range" min="0" max="100" step="5" value={floor} onChange={(e) => setFloor(+e.target.value)} aria-label="Minimum signal" />
          <span className="ii-result-count">{cases.length} / {DATA.cases.length} RECORDS</span>
        </div>
        <div className="ii-case-stack">
          {cases.map((c) => <IICaseCard key={c.id} c={c} ctx={ctx} />)}
          {!cases.length && <p className="ii-empty">NO RECORDS AT THIS FLOOR. LOWER SIG OR WIDEN CHANNEL.</p>}
        </div>
      </div>
      <aside className="ii-rail">
        <Panel title={`TECHNIQUES IN THESE ${cases.length} CASES`}>
          <div className="ii-rail-list">
            {topTech.map(([t, n]) => (
              <button key={t} type="button" className="ii-rail-row" onClick={() => ctx.openTech(t)}>
                <span className="ii-rail-id">{t}</span>
                <span className="ii-rail-name">{DATA.techniques[t].name}</span>
                <span className="ii-rail-bar"><i style={{ width: (n / cases.length * 100) + "%" }}></i></span>
                <span className="ii-rail-n">{n}</span>
              </button>
            ))}
          </div>
          <p className="ii-rail-note">Not trending — a tally of the cases listed on the left, updating as you filter. Click one for its technique dossier (detections + hunt logic).</p>
        </Panel>
        <Panel title="EVIDENCE TRAIL — CORPUS">
          <div className="ii-rail-list">
            {DATA.trail.slice(0, 4).map(([label, all, adj]) => (
              <div key={label} className="ii-rail-row ii-rail-row-static">
                <span className="ii-rail-name">{label}</span>
                <span className="ii-rail-bar"><i style={{ width: all + "%" }}></i><i className="ii-bar-adj" style={{ width: adj + "%" }}></i></span>
                <span className="ii-rail-n">{all}%</span>
              </div>
            ))}
          </div>
          <p className="ii-rail-note">Share of method-bearing cases whose record trail includes the class. Dark = confirmed in court. Full ledger on EVIDENCE.</p>
        </Panel>
      </aside>
    </div>
  );
}

function CaseScreen({ ctx, caseId }) {
  const c = DATA.cases.find((x) => x.id === caseId);
  if (!c) return <p className="ii-empty">RECORD NOT FOUND.</p>;
  const flagged = ctx.flagged.includes(c.id);
  return (
    <div className="ii-detail-grid">
      <div>
        <button type="button" className="ii-back" onClick={() => ctx.nav("stream")}>← STREAM</button>
        <IICaseCard c={c} ctx={ctx} full />
        <Panel title="OPERATOR SEARCH TERMS">
          <div className="ii-chip-row">{c.terms.map((t) => <Chip key={t}>{t}</Chip>)}</div>
          <div className="ii-action-row"><CopyButton onClick={() => navigator.clipboard && navigator.clipboard.writeText(c.terms.join("\n"))}>COPY TERMS</CopyButton></div>
        </Panel>
      </div>
      <aside className="ii-rail">
        <Panel title="TECHNIQUES IN THIS CASE">
          {c.itm.map((t) => {
            const tech = DATA.techniques[t];
            return (
              <button key={t} type="button" className="ii-tech-link" onClick={() => ctx.openTech(t)}>
                <span className="ii-rail-id">{t}</span>
                <span>
                  <b>{tech.name}</b>
                  <em>{tech.theme} · {tech.detections.length} DETECTIONS · {tech.hunts.length} HUNTS →</em>
                </span>
              </button>
            );
          })}
        </Panel>
        <Panel title="RECORD ACTIONS">
          <div className="ii-action-col">
            <CopyButton primary onClick={() => ctx.toggleFlag(c.id)}>{flagged ? "✓ ON EVIDENCE BOARD" : "+ FLAG TO BOARD"}</CopyButton>
            <CopyButton onClick={() => {}}>OPEN SOURCE RECORD ↗</CopyButton>
            <CopyButton onClick={() => {}}>MODUS OPERANDI</CopyButton>
          </div>
          <p className="ii-rail-note">Modus operandi is assembled from stored forensics — no model runs at read time.</p>
        </Panel>
      </aside>
    </div>
  );
}

Object.assign(window, { IICaseCard, caseMeta, StreamScreen, CaseScreen });
