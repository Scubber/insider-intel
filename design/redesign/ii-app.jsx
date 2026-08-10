/* Shell: masthead, status band, nav, footer, routing. Screens live on window (ii-screens*.jsx). */
const { DossierProvider } = window.DossierUI;
/* Same theme values, neutral labels — no media/brand references in the UI. */
const THEME_OPTIONS = [
  ["dossier", "Dossier"], ["Dossier Sage", "Dossier Sage"], ["Dossier Soft", "Dossier Soft"], ["Dossier Fog", "Dossier Fog"],
  ["cnn-lite", "Wire Light"], ["midnight", "Midnight"], ["phosphor", "Phosphor"],
  ["Air Archive", "Air Archive"], ["Cinder Archive", "Cinder Archive"], ["Ice Archive", "Ice Archive"], ["Earth Archive", "Earth Archive"],
  ["Cryostat", "Cryostat"], ["Vermillion Court", "Vermillion Court"],
  ["diablo", "Ember"], ["Diablo II", "Gilt Ember"], ["Doom 3", "Rust Terminal"],
  ["Ultramarines", "Cobalt"], ["Blood Ravens", "Oxblood"], ["Black Templars", "Obsidian"], ["Raven Guard", "Graphite"],
  ["StarCraft", "Void"], ["Brood War", "Ultraviolet"], ["GoldenEye 64", "Crimson Gold"], ["Warcraft III", "Banner Gold"],
  ["Bleach", "Ivory Ink"], ["Ultima Online", "Parchment"],
  ["Evangelion", "Violet"], ["EVA-01", "Violet Ops"], ["EVA-02", "Vermilion Ops"], ["EVA-03", "Onyx Ops"],
  ["Perplexity", "Teal Console"], ["Linear", "Indigo"], ["Vercel", "Monochrome"], ["ChatGPT", "Slate"]
];
function ThemePicker({ value, onChange }) {
  return (
    <label className="ds-theme-select">
      <span className="ds-theme-select-label">Theme</span>
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        {THEME_OPTIONS.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
      </select>
    </label>
  );
}
const D = window.II_DATA;

function useUtcClock() {
  const [now, setNow] = React.useState(() => new Date());
  React.useEffect(() => { const t = setInterval(() => setNow(new Date()), 60000); return () => clearInterval(t); }, []);
  const p = (n) => String(n).padStart(2, "0");
  return `${now.getUTCFullYear()}-${p(now.getUTCMonth() + 1)}-${p(now.getUTCDate())}`;
}

function relTime(iso) {
  const h = Math.max(0, (Date.now() - Date.parse(iso)) / 3600000);
  if (h < 1) return Math.max(1, Math.round(h * 60)) + "m ago";
  if (h < 48) return Math.round(h) + "h ago";
  return Math.round(h / 24) + "d ago";
}

function Masthead({ view, nav, flaggedCount, clock }) {
  const items = [["stream", "STREAM"], ["matrix", "MATRIX"], ["evidence", "EVIDENCE"], ["workbench", `WORKBENCH${flaggedCount ? " [" + flaggedCount + "]" : ""}`]];
  return (
    <header className="ii-masthead">
      <div className="ii-mast-row">
        <div className="ii-brand">
          <span className="ii-product">insider-intel</span>
          <span className="ii-corpus">CORPUS {D.status.corpus.toLocaleString()} · METHOD-BEARING {D.status.methodBearing.toLocaleString()} · CONFIRMED IN COURT {D.status.adjudicated}</span>
        </div>
        <nav className="ii-nav">
          {items.map(([k, label]) => (
            <button key={k} type="button" className={"ii-nav-item" + (view === k || (view === "case" && k === "stream") || (view === "technique" && k === "matrix") ? " active" : "")} onClick={() => nav(k)}>{label}</button>
          ))}
        </nav>
      </div>
      <div className="ii-statusline">
        <span>LAST INGEST {relTime(D.status.lastIngest.replace(" ", "T"))} — OK</span>
        <span className="ii-filter-label">FEEDS</span>
        {D.status.lanes.map(([lane, st]) => <span key={lane} className={st === "OK" ? "ii-lane-ok" : "ii-lane-idle"}>{lane} {st === "OK" ? "●" : "○"}</span>)}
        <span className="ii-clock">{clock}</span>
      </div>
    </header>
  );
}

function Footer({ theme, setTheme, nav }) {
  return (
    <footer className="ii-footer">
      <div className="ii-foot-left">
        <span>insider-intel — evidence-based insider-threat research, built from what actually reaches court.</span>
        <span className="ii-foot-links"><a href="#" onClick={(e) => { e.preventDefault(); nav("about"); }}>METHODOLOGY & COLOPHON</a> · <a href="https://insiderthreatmatrix.org/" target="_blank" rel="noopener">ITM™ © FORSCIE LTD — NOT AFFILIATED</a> · <a href="#" onClick={(e) => e.preventDefault()}>FEED.XML</a> · <a href="#" onClick={(e) => e.preventDefault()}>API</a></span>
        <span className="ii-kbd">j/k move · x flag · ⏎ open · / search</span>
      </div>
      <ThemePicker value={theme} onChange={setTheme} />
    </footer>
  );
}

function App() {
  const [theme, setTheme] = React.useState(() => localStorage.getItem("ii-redesign-theme-v4") || "dossier");
  const [view, setView] = React.useState("stream");
  const [caseId, setCaseId] = React.useState(null);
  const [techId, setTechId] = React.useState(null);
  const [flagged, setFlagged] = React.useState(() => { try { return JSON.parse(localStorage.getItem("ii-redesign-board") || "[]"); } catch { return []; } });
  React.useEffect(() => localStorage.setItem("ii-redesign-theme-v4", theme), [theme]);
  React.useEffect(() => localStorage.setItem("ii-redesign-board", JSON.stringify(flagged)), [flagged]);
  const clock = useUtcClock();

  const openCase = (id) => { setCaseId(id); setView("case"); };
  const openTech = (id) => { setTechId(id); setView("technique"); };
  const toggleFlag = (id) => setFlagged((f) => f.includes(id) ? f.filter((x) => x !== id) : [...f, id]);
  const nav = (v) => { if (v === "stream") setCaseId(null); if (v === "matrix") setTechId(null); setView(v); };
  const ctx = { openCase, openTech, toggleFlag, flagged, nav };

  let screen = null;
  if (view === "stream") screen = <StreamScreen ctx={ctx} />;
  else if (view === "case") screen = <CaseScreen ctx={ctx} caseId={caseId} />;
  else if (view === "matrix") screen = <MatrixScreen ctx={ctx} />;
  else if (view === "technique") screen = <TechniqueScreen ctx={ctx} techId={techId} />;
  else if (view === "workbench") screen = <WorkbenchScreen ctx={ctx} />;
  else if (view === "evidence") screen = <EvidenceScreen ctx={ctx} />;
  else if (view === "about") screen = <AboutScreen ctx={ctx} />;

  return (
    <DossierProvider theme={theme}>
      <div className="ii-app">
        <Masthead view={view} nav={nav} flaggedCount={flagged.length} clock={clock} />
        <main className="ii-main">{screen}</main>
        <Footer theme={theme} setTheme={setTheme} nav={nav} />
      </div>
    </DossierProvider>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);
