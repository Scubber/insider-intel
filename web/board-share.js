/* Extraction-board share codec — board links → compressed base64url payload
 * carried in the URL fragment (#/board/v1/<payload>), plus a plain fallback
 * (#/board/v1p/<payload>) for browsers without CompressionStream. Pure module
 * so it can be exercised from a console; no app state touched here. */
(() => {
  function bytesToBase64url(bytes) {
    let binary = "";
    const CHUNK = 0x8000;
    for (let i = 0; i < bytes.length; i += CHUNK) {
      binary += String.fromCharCode(...bytes.subarray(i, i + CHUNK));
    }
    return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }

  function base64urlToBytes(str) {
    const padded = str.replace(/-/g, "+").replace(/_/g, "/");
    const binary = atob(padded);
    return Uint8Array.from(binary, (c) => c.charCodeAt(0));
  }

  async function pipeThrough(bytes, TransformCtor, mode) {
    const stream = new Blob([bytes]).stream().pipeThrough(new TransformCtor(mode));
    return new Uint8Array(await new Response(stream).arrayBuffer());
  }

  /** encodeBoard(links) → { variant: "v1"|"v1p", payload } */
  async function encodeBoard(links) {
    const clean = (links || []).map((l) => String(l || "").trim()).filter(Boolean);
    const raw = new TextEncoder().encode(JSON.stringify({ v: 1, links: clean }));
    if (typeof CompressionStream === "function") {
      return {
        variant: "v1",
        payload: bytesToBase64url(await pipeThrough(raw, CompressionStream, "deflate-raw")),
      };
    }
    return { variant: "v1p", payload: bytesToBase64url(raw) };
  }

  /** decodeBoard(payload, variant) → links[]; throws on malformed payloads. */
  async function decodeBoard(payload, variant) {
    const bytes = base64urlToBytes(String(payload || ""));
    let raw;
    if (variant === "v1p") {
      raw = bytes;
    } else {
      if (typeof DecompressionStream !== "function") {
        throw new Error("browser cannot decompress this board link");
      }
      raw = await pipeThrough(bytes, DecompressionStream, "deflate-raw");
    }
    const data = JSON.parse(new TextDecoder().decode(raw));
    if (!data || data.v !== 1 || !Array.isArray(data.links)) {
      throw new Error("unrecognized board payload");
    }
    return data.links.map((l) => String(l || "").trim()).filter(Boolean);
  }

  window.InsiderIntelBoardShare = { encodeBoard, decodeBoard };
})();
