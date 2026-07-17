/**
 * Live API base URL, chosen by host.
 *
 * - Local (127.0.0.1 / localhost): FastAPI on :8000
 * - Public (intel.thederpweb.com and friends): Cloud Run API
 * See docs/hosting.md
 */
(function () {
  var host = window.location.hostname || "";
  var isPublic =
    host === "intel.thederpweb.com" ||
    host === "td3.dev" ||
    host === "www.td3.dev" ||
    host === "scubber.github.io" ||
    /\.github\.io$/i.test(host);

  if (typeof window.INSIDER_INTEL_API_BASE === "undefined") {
    window.INSIDER_INTEL_API_BASE = isPublic
      ? "https://api.intel.thederpweb.com"
      : "http://127.0.0.1:8000";
  }
})();
