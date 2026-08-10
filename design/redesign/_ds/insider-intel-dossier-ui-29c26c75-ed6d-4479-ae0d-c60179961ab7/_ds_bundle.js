/* @ds-bundle: {"namespace":"DossierUI","components":[{"name":"ActionButton","sourcePath":"components/general/ActionButton/ActionButton.jsx"},{"name":"CaseCard","sourcePath":"components/general/CaseCard/CaseCard.jsx"},{"name":"Chip","sourcePath":"components/general/Chip/Chip.jsx"},{"name":"CopyButton","sourcePath":"components/general/CopyButton/CopyButton.jsx"},{"name":"DossierProvider","sourcePath":"components/general/DossierProvider/DossierProvider.jsx"},{"name":"FactList","sourcePath":"components/general/FactList/FactList.jsx"},{"name":"ItmChip","sourcePath":"components/general/ItmChip/ItmChip.jsx"},{"name":"Panel","sourcePath":"components/general/Panel/Panel.jsx"},{"name":"Pill","sourcePath":"components/general/Pill/Pill.jsx"},{"name":"TechniqueSection","sourcePath":"components/general/TechniqueSection/TechniqueSection.jsx"},{"name":"ThemeSelect","sourcePath":"components/general/ThemeSelect/ThemeSelect.jsx"}],"sourceHashes":{"components/general/ActionButton/ActionButton.jsx":"230bb1810365","components/general/ActionButton/ActionButton.d.ts":"3e36586571dd","components/general/ActionButton/ActionButton.prompt.md":"23fa29c9c8ae","components/general/CaseCard/CaseCard.jsx":"0d371410a317","components/general/CaseCard/CaseCard.d.ts":"caa6f14d0a15","components/general/CaseCard/CaseCard.prompt.md":"ecb01fb3a1b0","components/general/Chip/Chip.jsx":"f9f75cb317ad","components/general/Chip/Chip.d.ts":"b122b72274a5","components/general/Chip/Chip.prompt.md":"a6f409ca9e1d","components/general/CopyButton/CopyButton.jsx":"8ca11c216b0f","components/general/CopyButton/CopyButton.d.ts":"f5ce1a030932","components/general/CopyButton/CopyButton.prompt.md":"f18b91502550","components/general/DossierProvider/DossierProvider.jsx":"d9425818d95a","components/general/DossierProvider/DossierProvider.d.ts":"4412b097a278","components/general/DossierProvider/DossierProvider.prompt.md":"05d17598bad6","components/general/FactList/FactList.jsx":"036e30274b3c","components/general/FactList/FactList.d.ts":"8957f8672836","components/general/FactList/FactList.prompt.md":"f0a393bc9dae","components/general/ItmChip/ItmChip.jsx":"2dcbc73b6881","components/general/ItmChip/ItmChip.d.ts":"d99c83484c30","components/general/ItmChip/ItmChip.prompt.md":"aa4ffe5788ed","components/general/Panel/Panel.jsx":"3d09bae3ebf1","components/general/Panel/Panel.d.ts":"05b149c31090","components/general/Panel/Panel.prompt.md":"c473c66b1811","components/general/Pill/Pill.jsx":"c6724d19c491","components/general/Pill/Pill.d.ts":"2b4936059d8e","components/general/Pill/Pill.prompt.md":"6984667dc507","components/general/TechniqueSection/TechniqueSection.jsx":"5bac31ce0e77","components/general/TechniqueSection/TechniqueSection.d.ts":"9f4f3375b57c","components/general/TechniqueSection/TechniqueSection.prompt.md":"f53f4af79565","components/general/ThemeSelect/ThemeSelect.jsx":"da23533291a5","components/general/ThemeSelect/ThemeSelect.d.ts":"ac9927f169c6","components/general/ThemeSelect/ThemeSelect.prompt.md":"79d86bf65db9"},"inlinedExternals":[],"builtBy":"cc-design-sync"} */
"use strict";
var DossierUI = (() => {
  var __create = Object.create;
  var __defProp = Object.defineProperty;
  var __getOwnPropDesc = Object.getOwnPropertyDescriptor;
  var __getOwnPropNames = Object.getOwnPropertyNames;
  var __getProtoOf = Object.getPrototypeOf;
  var __hasOwnProp = Object.prototype.hasOwnProperty;
  var __esm = (fn, res, err) => function __init() {
    if (err) throw err[0];
    try {
      return fn && (res = (0, fn[__getOwnPropNames(fn)[0]])(fn = 0)), res;
    } catch (e) {
      throw err = [e], e;
    }
  };
  var __commonJS = (cb, mod) => function __require() {
    try {
      return mod || (0, cb[__getOwnPropNames(cb)[0]])((mod = { exports: {} }).exports, mod), mod.exports;
    } catch (e) {
      throw mod = 0, e;
    }
  };
  var __export = (target, all) => {
    for (var name in all)
      __defProp(target, name, { get: all[name], enumerable: true });
  };
  var __copyProps = (to, from, except, desc) => {
    if (from && typeof from === "object" || typeof from === "function") {
      for (let key of __getOwnPropNames(from))
        if (!__hasOwnProp.call(to, key) && key !== except)
          __defProp(to, key, { get: () => from[key], enumerable: !(desc = __getOwnPropDesc(from, key)) || desc.enumerable });
    }
    return to;
  };
  var __toESM = (mod, isNodeMode, target) => (target = mod != null ? __create(__getProtoOf(mod)) : {}, __copyProps(
    // If the importer is in node compatibility mode or this is not an ESM
    // file that has been converted to a CommonJS file using a Babel-
    // compatible transform (i.e. "__esModule" has not been set), then set
    // "default" to the CommonJS "module.exports" for node compatibility.
    isNodeMode || !mod || !mod.__esModule ? __defProp(target, "default", { value: mod, enumerable: true }) : target,
    mod
  ));
  var __toCommonJS = (mod) => __copyProps(__defProp({}, "__esModule", { value: true }), mod);

  // <define:import.meta.env>
  var init_define_import_meta_env = __esm({
    "<define:import.meta.env>"() {
    }
  });

  // shim:react-shim
  var require_react_shim = __commonJS({
    "shim:react-shim"(exports, module) {
      init_define_import_meta_env();
      var R = window.React;
      function np(p, k) {
        var o = {};
        for (var x in p) if (x !== "children") o[x] = p[x];
        if (k !== void 0) o.key = k;
        return o;
      }
      function jsx9(t, p, k) {
        var c = p && p.children;
        return c === void 0 ? R.createElement(t, np(p, k)) : R.createElement(t, np(p, k), c);
      }
      function jsxs6(t, p, k) {
        return R.createElement.apply(R, [t, np(p, k)].concat(p.children));
      }
      module.exports = R;
      module.exports.jsx = jsx9;
      module.exports.jsxs = jsxs6;
      module.exports.jsxDEV = function(t, p, k, s) {
        return (s ? jsxs6 : jsx9)(t, p, k);
      };
      module.exports.Fragment = R.Fragment;
    }
  });

  // design-system/dist/index.js
  var index_exports = {};
  __export(index_exports, {
    ActionButton: () => ActionButton,
    CaseCard: () => CaseCard,
    Chip: () => Chip,
    CopyButton: () => CopyButton,
    DOSSIER_THEMES: () => DOSSIER_THEMES,
    DossierProvider: () => DossierProvider,
    FactList: () => FactList,
    ItmChip: () => ItmChip,
    Panel: () => Panel,
    Pill: () => Pill,
    TechniqueSection: () => TechniqueSection,
    ThemeSelect: () => ThemeSelect
  });
  init_define_import_meta_env();
  var import_jsx_runtime = __toESM(require_react_shim(), 1);
  var import_jsx_runtime2 = __toESM(require_react_shim(), 1);
  var import_jsx_runtime3 = __toESM(require_react_shim(), 1);
  var import_jsx_runtime4 = __toESM(require_react_shim(), 1);
  var import_jsx_runtime5 = __toESM(require_react_shim(), 1);
  var import_jsx_runtime6 = __toESM(require_react_shim(), 1);
  var import_jsx_runtime7 = __toESM(require_react_shim(), 1);
  var import_jsx_runtime8 = __toESM(require_react_shim(), 1);
  function DossierProvider({ theme = "Dossier Sage", children }) {
    return /* @__PURE__ */ (0, import_jsx_runtime.jsx)("div", { className: "ds-root", "data-theme": theme, children });
  }
  function Panel({ title, children }) {
    return /* @__PURE__ */ (0, import_jsx_runtime2.jsxs)("section", { className: "ds-panel", children: [
      title ? /* @__PURE__ */ (0, import_jsx_runtime2.jsx)("h2", { className: "ds-panel-title", children: title }) : null,
      children
    ] });
  }
  function FactList({ items }) {
    return /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("dl", { className: "ds-facts", children: items.map((fact) => /* @__PURE__ */ (0, import_jsx_runtime3.jsx)(FactRow, { ...fact }, fact.label + fact.value)) });
  }
  function FactRow({ label, value }) {
    return /* @__PURE__ */ (0, import_jsx_runtime3.jsxs)(import_jsx_runtime3.Fragment, { children: [
      /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("dt", { children: label }),
      /* @__PURE__ */ (0, import_jsx_runtime3.jsx)("dd", { children: value })
    ] });
  }
  function CaseCard({ tab, title, meta, note, facts, footer, actions }) {
    return /* @__PURE__ */ (0, import_jsx_runtime4.jsxs)("article", { className: "ds-case", children: [
      /* @__PURE__ */ (0, import_jsx_runtime4.jsx)("div", { className: "ds-case-tab", children: tab }),
      /* @__PURE__ */ (0, import_jsx_runtime4.jsxs)("div", { className: "ds-case-body", children: [
        /* @__PURE__ */ (0, import_jsx_runtime4.jsx)("h3", { className: "ds-case-title", children: title }),
        meta ? /* @__PURE__ */ (0, import_jsx_runtime4.jsx)("p", { className: "ds-case-meta", children: meta }) : null,
        facts && facts.length ? /* @__PURE__ */ (0, import_jsx_runtime4.jsx)(FactList, { items: facts }) : null,
        note ? /* @__PURE__ */ (0, import_jsx_runtime4.jsx)("p", { className: "ds-case-note", children: note }) : null
      ] }),
      /* @__PURE__ */ (0, import_jsx_runtime4.jsxs)("div", { className: "ds-case-footer", children: [
        footer,
        actions ? /* @__PURE__ */ (0, import_jsx_runtime4.jsx)("div", { className: "ds-case-actions", children: actions }) : null
      ] })
    ] });
  }
  function Chip({ children, signal = false }) {
    return /* @__PURE__ */ (0, import_jsx_runtime5.jsx)("span", { className: signal ? "ds-chip ds-chip--signal" : "ds-chip", children });
  }
  function ItmChip({ id, title, onClick }) {
    return /* @__PURE__ */ (0, import_jsx_runtime5.jsx)("button", { type: "button", className: "ds-itm-chip", title, onClick, children: id });
  }
  function Pill({ children, active = false, onClick }) {
    return /* @__PURE__ */ (0, import_jsx_runtime5.jsx)(
      "button",
      {
        type: "button",
        className: active ? "ds-pill ds-pill--active" : "ds-pill",
        onClick,
        children
      }
    );
  }
  function ActionButton({ children, active = false, onClick }) {
    return /* @__PURE__ */ (0, import_jsx_runtime6.jsx)(
      "button",
      {
        type: "button",
        className: active ? "ds-action-btn ds-action-btn--active" : "ds-action-btn",
        onClick,
        children
      }
    );
  }
  function CopyButton({ children, primary = false, onClick }) {
    return /* @__PURE__ */ (0, import_jsx_runtime6.jsx)(
      "button",
      {
        type: "button",
        className: primary ? "ds-copy-btn ds-copy-btn--primary" : "ds-copy-btn",
        onClick,
        children
      }
    );
  }
  function TechniqueSection({ id, description, cases = [] }) {
    return /* @__PURE__ */ (0, import_jsx_runtime7.jsxs)("article", { className: "ds-technique", children: [
      /* @__PURE__ */ (0, import_jsx_runtime7.jsxs)("p", { className: "ds-technique-head", children: [
        /* @__PURE__ */ (0, import_jsx_runtime7.jsx)("span", { className: "ds-technique-id", children: id }),
        description
      ] }),
      cases.map((item) => /* @__PURE__ */ (0, import_jsx_runtime7.jsx)(TechniqueCaseBlock, { ...item }, item.title))
    ] });
  }
  function TechniqueCaseBlock({ title, bullets }) {
    return /* @__PURE__ */ (0, import_jsx_runtime7.jsxs)(import_jsx_runtime7.Fragment, { children: [
      /* @__PURE__ */ (0, import_jsx_runtime7.jsx)("p", { className: "ds-technique-case", children: title }),
      bullets.length ? /* @__PURE__ */ (0, import_jsx_runtime7.jsx)("ul", { className: "ds-technique-bullets", children: bullets.map((bullet) => /* @__PURE__ */ (0, import_jsx_runtime7.jsx)("li", { children: bullet }, bullet)) }) : null
    ] });
  }
  var DOSSIER_THEMES = [
    "dossier",
    "midnight",
    "phosphor",
    "cnn-lite",
    "diablo",
    "Dossier Sage",
    "Dossier Soft",
    "Dossier Fog",
    "Air Archive",
    "Cinder Archive",
    "Ice Archive",
    "Earth Archive",
    "Ultramarines",
    "Blood Ravens",
    "Black Templars",
    "Raven Guard",
    "Perplexity",
    "Linear",
    "Vercel",
    "ChatGPT",
    "Doom 3",
    "Diablo II",
    "StarCraft",
    "Brood War",
    "GoldenEye 64",
    "Warcraft III",
    "Bleach",
    "Ultima Online",
    "Evangelion",
    "EVA-01",
    "EVA-02",
    "EVA-03",
    "Cryostat",
    "Vermillion Court"
  ];
  var THEME_LABELS = {
    dossier: "Dossier",
    midnight: "Midnight",
    phosphor: "Phosphor",
    "cnn-lite": "CNN Lite",
    diablo: "Diablo",
    "Dossier Sage": "Dossier Sage",
    "Dossier Soft": "Dossier Soft",
    "Dossier Fog": "Dossier Fog",
    "Air Archive": "Air Archive",
    "Cinder Archive": "Cinder Archive",
    "Ice Archive": "Ice Archive",
    "Earth Archive": "Earth Archive",
    Ultramarines: "Ultramarines",
    Perplexity: "Perplexity",
    Linear: "Linear",
    Vercel: "Vercel",
    ChatGPT: "ChatGPT",
    "Doom 3": "Doom 3",
    "Diablo II": "Diablo II",
    StarCraft: "StarCraft",
    "Brood War": "Brood War",
    "GoldenEye 64": "GoldenEye 64",
    "Warcraft III": "Warcraft III",
    Bleach: "Bleach",
    "Ultima Online": "Ultima Online",
    Evangelion: "Evangelion",
    "EVA-01": "EVA-01",
    "EVA-02": "EVA-02",
    "EVA-03": "EVA-03",
    Cryostat: "Cryostat",
    "Vermillion Court": "Vermillion Court",
    "Blood Ravens": "Blood Ravens",
    "Black Templars": "Black Templars",
    "Raven Guard": "Raven Guard"
  };
  function ThemeSelect({ value, onChange, themes = DOSSIER_THEMES }) {
    return /* @__PURE__ */ (0, import_jsx_runtime8.jsxs)("label", { className: "ds-theme-select", children: [
      /* @__PURE__ */ (0, import_jsx_runtime8.jsx)("span", { className: "ds-theme-select-label", children: "Theme" }),
      /* @__PURE__ */ (0, import_jsx_runtime8.jsx)(
        "select",
        {
          value,
          onChange: (event) => onChange?.(event.target.value),
          children: themes.map((theme) => /* @__PURE__ */ (0, import_jsx_runtime8.jsx)("option", { value: theme, children: THEME_LABELS[theme] ?? theme }, theme))
        }
      )
    ] });
  }
  return __toCommonJS(index_exports);
})();
window.DossierUI=DossierUI.__dsMainNs?Object.assign({},DossierUI,DossierUI.__dsMainNs,{__dsMainNs:undefined}):DossierUI;
