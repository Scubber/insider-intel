/**
 * Local / hosted API base URL, or optional static demo snapshot.
 *
 * - Local default: live FastAPI on :8000
 * - Public (intel.thederpweb.com): prefers live API at api.intel.thederpweb.com;
 *   app.js auto-falls back to web/demo when that API is unreachable
 * - Force static snapshot: ?demo=1
 * - Force live (no fallback): ?demo=0
 * See docs/hosting.md
 */
(function () {
  var host = window.location.hostname || "";
  var isLocal = host === "127.0.0.1" || host === "localhost" || host === "";
  var isPublic =
    host === "intel.thederpweb.com" ||
    host === "td3.dev" ||
    host === "www.td3.dev" ||
    host === "scubber.github.io" ||
    /\.github\.io$/i.test(host);
  var params = new URLSearchParams(window.location.search || "");
  var forceDemo = params.get("demo") === "1" || params.get("demo") === "true";
  var forceLive = params.get("demo") === "0";

  if (typeof window.INSIDER_INTEL_DEMO === "undefined") {
    if (forceDemo) {
      window.INSIDER_INTEL_DEMO = true;
    } else if (forceLive) {
      window.INSIDER_INTEL_DEMO = false;
    } else {
      // Prefer live API; boot() falls back to demo-store if public API is down.
      window.INSIDER_INTEL_DEMO = false;
    }
  }

  if (window.INSIDER_INTEL_DEMO) {
    window.INSIDER_INTEL_API_BASE = window.INSIDER_INTEL_API_BASE || "";
  } else if (isPublic) {
    window.INSIDER_INTEL_API_BASE =
      window.INSIDER_INTEL_API_BASE || "https://api.intel.thederpweb.com";
  } else {
    window.INSIDER_INTEL_API_BASE =
      window.INSIDER_INTEL_API_BASE || "http://127.0.0.1:8000";
  }
})();
