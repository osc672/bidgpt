/*!
 * BidGPT embeddable widget
 * Usage:
 *   <script src="https://YOUR-DOMAIN/widget.js" data-site="SITE_KEY"></script>
 *
 * Optional: data-api="https://api.example.com" (defaults to script origin)
 * Optional: data-theme="light|dark" (defaults to system preference)
 */
(function () {
  "use strict";

  var script =
    document.currentScript ||
    document.querySelector("script[data-site][src*='widget.js']");
  if (!script) return;
  if (window.__bidgptWidgetLoaded) return;
  window.__bidgptWidgetLoaded = true;

  var siteKey = (script.getAttribute("data-site") || "").trim();
  if (!siteKey) {
    console.error("[BidGPT] missing data-site attribute");
    return;
  }
  var apiBase = (script.getAttribute("data-api") || "").trim();
  if (!apiBase) {
    try {
      apiBase = new URL(script.src, window.location.href).origin;
    } catch (e) {
      console.error("[BidGPT] could not determine API base URL");
      return;
    }
  }
  apiBase = apiBase.replace(/\/+$/, "");
  var forcedTheme = (script.getAttribute("data-theme") || "").toLowerCase();

  var state = {
    open: false,
    ready: false,
    loading: false,
    status: null,
    history: [],
    greeted: false,
    pollTimer: null,
  };

  var CSS = [
    "#bidgpt-root{--bg:#ffffff;--surface:#f8fafc;--text:#0f172a;--muted:#64748b;--brand:#0f766e;--brand-ink:#ffffff;--border:#e2e8f0;--danger:#dc2626;--shadow:0 24px 64px -12px rgba(15,23,42,.25);--radius:18px;position:fixed;right:20px;bottom:20px;z-index:2147483000;font-family:system-ui,-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:var(--text);color-scheme:light dark}",
    "#bidgpt-root[data-theme='dark']{--bg:#0b1120;--surface:#111c30;--text:#f1f5f9;--muted:#94a3b8;--brand:#14b8a6;--brand-ink:#042f2e;--border:#1e293b;--danger:#f87171;--shadow:0 24px 64px -12px rgba(0,0,0,.6)}",
    "@media (prefers-color-scheme:dark){#bidgpt-root:not([data-theme='light']){--bg:#0b1120;--surface:#111c30;--text:#f1f5f9;--muted:#94a3b8;--brand:#14b8a6;--brand-ink:#042f2e;--border:#1e293b;--danger:#f87171;--shadow:0 24px 64px -12px rgba(0,0,0,.6)}}",
    "#bidgpt-root *{box-sizing:border-box}",
    "#bidgpt-launcher{display:flex;align-items:center;gap:10px;border:0;border-radius:999px;background:linear-gradient(135deg,#0f766e 0%,#0d9488 100%);color:var(--brand-ink);padding:13px 20px;font:600 14px/1 system-ui,-apple-system,sans-serif;cursor:pointer;box-shadow:0 10px 28px rgba(15,118,110,.35);transition:all .2s cubic-bezier(.4,0,.2,1)}",
    "#bidgpt-launcher:hover{transform:translateY(-2px);box-shadow:0 14px 34px rgba(15,118,110,.45)}",
    "#bidgpt-launcher:focus-visible,#bidgpt-close:focus-visible,#bidgpt-send:focus-visible,#bidgpt-input:focus-visible,a.bidgpt-source:focus-visible,.bidgpt-view-all-btn:focus-visible{outline:3px solid color-mix(in srgb,var(--brand) 45%,transparent);outline-offset:2px}",
    "#bidgpt-launcher svg{width:20px;height:20px;flex:none}",
    "#bidgpt-panel{position:absolute;right:0;bottom:70px;width:min(410px,calc(100vw - 28px));max-height:min(680px,calc(100vh - 100px));display:none;flex-direction:column;background:var(--bg);border:1px solid var(--border);border-radius:var(--radius);box-shadow:var(--shadow);overflow:hidden;backdrop-filter:blur(8px)}",
    "#bidgpt-panel[data-open='true']{display:flex}",
    "#bidgpt-head{display:flex;align-items:center;justify-content:space-between;padding:16px 18px;border-bottom:1px solid var(--border);background:linear-gradient(180deg,color-mix(in srgb,var(--brand) 10%,var(--bg)),var(--bg))}",
    "#bidgpt-title{font-size:16.5px;font-weight:700;margin:0;letter-spacing:-.015em;display:flex;align-items:center;gap:8px}",
    "#bidgpt-sub{font-size:12px;color:var(--muted);margin:2px 0 0}",
    "#bidgpt-close{width:32px;height:32px;border:1px solid var(--border);border-radius:10px;background:var(--surface);color:var(--text);font-size:20px;line-height:1;display:flex;align-items:center;justify-content:center;cursor:pointer;flex:none;transition:all .15s ease}",
    "#bidgpt-close:hover{background:var(--border);transform:scale(1.05)}",
    "#bidgpt-status{font-size:12px;font-weight:500;padding:8px 18px;background:var(--surface);color:var(--muted);border-bottom:1px solid var(--border);display:flex;align-items:center;gap:8px;min-height:34px}",
    "#bidgpt-status[data-error='true']{color:var(--danger)}",
    ".bidgpt-dot{width:7.5px;height:7.5px;border-radius:50%;background:var(--brand);flex:none}",
    ".bidgpt-dot.bidgpt-pulse{animation:bidgptPulse 1.4s ease-in-out infinite}",
    "@keyframes bidgptPulse{0%,100%{opacity:.4;transform:scale(.9)}50%{opacity:1;transform:scale(1.15)}}",
    "@media (prefers-reduced-motion:reduce){.bidgpt-dot.bidgpt-pulse{animation:none}#bidgpt-launcher{transition:none}}",
    "#bidgpt-log{flex:1;overflow-y:auto;padding:16px 18px;display:flex;flex-direction:column;gap:14px;scroll-behavior:smooth}",
    "@media (prefers-reduced-motion:reduce){#bidgpt-log{scroll-behavior:auto}}",
    ".bidgpt-msg{max-width:92%;font-size:13.5px;line-height:1.5;white-space:pre-wrap;overflow-wrap:anywhere}",
    ".bidgpt-msg.bidgpt-user{align-self:flex-end;background:linear-gradient(135deg,#0f766e 0%,#0d9488 100%);color:var(--brand-ink);padding:10px 14px;border-radius:16px 16px 4px 16px;box-shadow:0 2px 8px rgba(15,118,110,.25)}",
    ".bidgpt-msg.bidgpt-bot{align-self:flex-start;color:var(--text)}",
    ".bidgpt-msg.bidgpt-bot.bidgpt-plain{background:var(--surface);border:1px solid var(--border);padding:10px 14px;border-radius:16px 16px 16px 4px}",
    ".bidgpt-cards-container{display:flex;flex-direction:column;gap:10px;width:100%}",
    ".bidgpt-cards-list{display:flex;flex-direction:column;gap:12px;width:100%}",
    ".bidgpt-chips-scroll-wrap{display:flex;align-items:center;gap:4px;width:100%;margin-bottom:4px;position:relative}",
    ".bidgpt-chip-nav-btn{width:26px;height:26px;border-radius:50%;border:1px solid var(--border);background:var(--surface);color:var(--text);display:flex;align-items:center;justify-content:center;cursor:pointer;flex-shrink:0;transition:all .15s ease;box-shadow:0 1px 3px rgba(0,0,0,.08)}",
    ".bidgpt-chip-nav-btn:hover{background:var(--brand);color:#ffffff;border-color:var(--brand);transform:scale(1.08)}",
    ".bidgpt-filter-chips{display:flex;align-items:center;gap:6px;overflow-x:auto;padding:2px 2px;margin:0;-webkit-overflow-scrolling:touch;scrollbar-width:none;flex:1;scroll-behavior:smooth}",
    ".bidgpt-filter-chips::-webkit-scrollbar{display:none}",
    ".bidgpt-dual-filter-container{display:flex;flex-direction:column;gap:8px;margin:3px 0 6px;padding:8px 10px;background:var(--surface);border:1px solid var(--border);border-radius:12px}",
    ".bidgpt-filter-row{display:flex;flex-direction:column;gap:4px}",
    ".bidgpt-filter-row-header{display:flex;align-items:center;justify-content:space-between;padding:1px 4px}",
    ".bidgpt-filter-row-title{font-size:10.5px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.04em;display:inline-flex;align-items:center;gap:5px}",
    ".bidgpt-filter-row-title svg{width:12px;height:12px;color:var(--brand)}",
    ".bidgpt-empty-combo{padding:20px 14px;text-align:center;color:var(--muted);font-size:12.5px;background:var(--surface);border:1px dashed var(--border);border-radius:12px;margin:8px 0}",
    ".bidgpt-empty-combo p{margin:0 0 10px}",
    ".bidgpt-empty-combo strong{color:var(--text)}",
    ".bidgpt-reset-filter-btn{padding:6px 14px;border-radius:6px;border:1px solid var(--brand);background:var(--brand);color:var(--brand-ink);font:600 11.5px/1 system-ui,sans-serif;cursor:pointer;transition:all .15s ease}",
    ".bidgpt-reset-filter-btn:hover{opacity:.9;transform:translateY(-1px)}",
    ".bidgpt-chip{display:inline-flex;align-items:center;gap:4px;padding:6px 12px;border-radius:999px;border:1px solid var(--border);background:var(--surface);color:var(--muted);font:600 11.5px/1 system-ui,-apple-system,sans-serif;cursor:pointer;white-space:nowrap;transition:all .15s ease;flex-shrink:0}",
    ".bidgpt-chip:hover{border-color:var(--brand);color:var(--text);background:var(--bg)}",
    ".bidgpt-chip.active{background:var(--brand);border-color:var(--brand);color:var(--brand-ink);box-shadow:0 2px 8px rgba(15,118,110,.25)}",
    ".bidgpt-pagination-bar{display:flex;align-items:center;justify-content:space-between;padding:8px 12px;background:var(--surface);border:1px solid var(--border);border-radius:12px;gap:8px}",
    ".bidgpt-top-paging{margin-bottom:2px;margin-top:2px}",
    ".bidgpt-bottom-paging{margin-top:6px;margin-bottom:2px}",
    ".bidgpt-page-btn{display:inline-flex;align-items:center;gap:5px;padding:6px 12px;border-radius:8px;border:1px solid var(--border);background:var(--bg);color:var(--text);font:600 12px/1 system-ui,-apple-system,sans-serif;cursor:pointer;transition:all .15s ease}",
    ".bidgpt-page-btn:hover:not(:disabled){border-color:var(--brand);color:var(--brand);background:color-mix(in srgb,var(--brand) 8%,var(--bg));transform:translateY(-1px)}",
    ".bidgpt-page-btn:disabled{opacity:.35;cursor:not-allowed;transform:none}",
    ".bidgpt-page-info{font-size:12px;color:var(--muted);display:inline-flex;align-items:center;gap:5px;white-space:nowrap}",
    ".bidgpt-page-info strong{color:var(--text);font-weight:700}",
    ".bidgpt-page-range{font-size:11px;color:var(--muted);opacity:.85}",
    ".bidgpt-card{border:1px solid var(--border);background:var(--bg);border-radius:14px;overflow:hidden;box-shadow:0 3px 12px -2px rgba(15,23,42,.07);transition:transform .2s ease,box-shadow .2s ease}",
    ".bidgpt-card:hover{transform:translateY(-2px);box-shadow:0 8px 24px -4px rgba(15,23,42,.12)}",
    ".bidgpt-card-hidden{display:none!important}",
    ".bidgpt-card-fadein{animation:bidgptCardIn .25s ease-out forwards}",
    "@keyframes bidgptCardIn{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:translateY(0)}}",
    ".bidgpt-card-header{background:linear-gradient(135deg,#0f766e 0%,#0d9488 100%);color:#ffffff;padding:9px 13px;display:flex;align-items:center;justify-content:space-between;font-size:12px;font-weight:600}",
    ".bidgpt-card-header-left,.bidgpt-card-header-right{display:inline-flex;align-items:center;gap:5px}",
    ".bidgpt-card-header-right{background:rgba(255,255,255,.2);padding:2px 8px;border-radius:999px;font-size:11px}",
    ".bidgpt-card-body{padding:12px 14px;display:flex;flex-direction:column;gap:9px;background:var(--bg);color:var(--text)}",
    ".bidgpt-card-info{font-size:13.5px;font-weight:600;line-height:1.45;color:var(--text);margin:0}",
    ".bidgpt-card-info-label{font-weight:700;color:var(--brand)}",
    ".bidgpt-card-closing{display:flex;align-items:center}",
    ".bidgpt-card-closing-badge{display:inline-flex;align-items:center;gap:6px;padding:4px 9px;border-radius:6px;font-size:11.5px;font-weight:500;background:#fef2f2;color:#b91c1c;border:1px solid #fee2e2;align-self:flex-start}",
    "#bidgpt-root[data-theme='dark'] .bidgpt-card-closing-badge{background:rgba(239,68,68,.14);color:#fca5a5;border-color:rgba(239,68,68,.25)}",
    ".bidgpt-card-closing-val{font-weight:700}",
    ".bidgpt-metrics-row{display:grid;grid-template-columns:repeat(3,1fr);gap:6px;margin-top:2px}",
    ".bidgpt-metric-item{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:6px 8px;display:flex;flex-direction:column;gap:2px}",
    ".bidgpt-metric-lbl{font-size:10px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.03em}",
    ".bidgpt-metric-val-emd,.bidgpt-metric-val-exemption,.bidgpt-metric-val-ecv{font-size:12px;font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}",
    ".bidgpt-metric-val-emd{color:#7c3aed}",
    ".bidgpt-metric-val-exemption{color:#059669}",
    ".bidgpt-metric-val-ecv{color:#2563eb}",
    ".bidgpt-card-actions{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-top:4px;padding-top:6px;border-top:1px dashed var(--border)}",
    ".bidgpt-share-icons{display:inline-flex;align-items:center;gap:6px}",
    ".bidgpt-share-btn{width:26px;height:26px;border-radius:6px;border:0;padding:0;display:inline-flex;align-items:center;justify-content:center;cursor:pointer;color:#ffffff;transition:opacity .15s ease,transform .15s ease}",
    ".bidgpt-share-btn:hover{opacity:.88;transform:translateY(-1px)}",
    ".bidgpt-share-fb{background:#3b5998}",
    ".bidgpt-share-tw{background:#1da1f2}",
    ".bidgpt-share-in{background:#0077b5}",
    ".bidgpt-share-wa{background:#25d366}",
    ".bidgpt-share-btn svg{width:13px;height:13px;fill:currentColor}",
    ".bidgpt-view-btn,.bidgpt-source{display:inline-flex;align-items:center;gap:6px;padding:7px 14px;border:0;border-radius:8px;background:linear-gradient(135deg,#0f766e 0%,#0d9488 100%);color:#ffffff!important;font:600 12px/1 system-ui,sans-serif;cursor:pointer;text-decoration:none;box-shadow:0 2px 6px rgba(15,118,110,.25);transition:all .15s ease}",
    ".bidgpt-view-btn:hover,.bidgpt-source:hover{background:#0d9488;transform:translateY(-1px);box-shadow:0 4px 12px rgba(15,118,110,.35);opacity:1}",
    ".bidgpt-view-all-wrap{display:flex;justify-content:center;margin:8px 0 4px;width:100%}",
    ".bidgpt-view-all-btn{display:inline-flex;align-items:center;justify-content:center;gap:8px;width:100%;padding:10px 16px;border-radius:10px;background:color-mix(in srgb,var(--brand) 10%,var(--bg));border:1.5px solid var(--brand);color:var(--brand);font:700 13px/1 system-ui,-apple-system,sans-serif;cursor:pointer;transition:all .2s ease;box-shadow:0 2px 8px rgba(15,118,110,.1)}",
    ".bidgpt-view-all-btn:hover{background:var(--brand);color:var(--brand-ink);transform:translateY(-1px);box-shadow:0 6px 16px rgba(15,118,110,.25)}",
    "#bidgpt-form{display:flex;gap:8px;padding:12px 14px;border-top:1px solid var(--border);background:var(--bg)}",
    "#bidgpt-input{flex:1;min-width:0;border:1.5px solid var(--border);border-radius:12px;background:var(--surface);color:var(--text);padding:10px 14px;font:14px/1.3 system-ui,-apple-system,sans-serif;transition:border-color .15s ease}",
    "#bidgpt-input:focus{border-color:var(--brand);background:var(--bg)}",
    "#bidgpt-input::placeholder{color:var(--muted)}",
    "#bidgpt-send{border:0;border-radius:12px;background:linear-gradient(135deg,#0f766e 0%,#0d9488 100%);color:var(--brand-ink);font:600 13px/1 system-ui,-apple-system,sans-serif;padding:0 16px;cursor:pointer;min-height:42px;box-shadow:0 2px 8px rgba(15,118,110,.25);transition:all .15s ease}",
    "#bidgpt-send:hover:not(:disabled){transform:translateY(-1px);box-shadow:0 4px 12px rgba(15,118,110,.35)}",
    "#bidgpt-send:disabled,#bidgpt-input:disabled{opacity:.55;cursor:not-allowed}",
    ".bidgpt-sr{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}",
    "@media (max-width:480px){#bidgpt-root{right:12px;bottom:12px;left:12px}#bidgpt-launcher{margin-left:auto}#bidgpt-panel{position:fixed;left:12px;right:12px;bottom:76px;width:auto;max-height:calc(100vh - 100px)}}"
  ].join("\n");

  var style = document.createElement("style");
  style.setAttribute("data-bidgpt", "styles");
  style.textContent = CSS;
  (document.head || document.documentElement).appendChild(style);

  var root = document.createElement("div");
  root.id = "bidgpt-root";
  if (forcedTheme === "light" || forcedTheme === "dark") {
    root.setAttribute("data-theme", forcedTheme);
  }
  root.innerHTML =
    '<section id="bidgpt-panel" role="dialog" aria-modal="false" aria-labelledby="bidgpt-title" data-open="false">' +
    '<div id="bidgpt-head"><div><h2 id="bidgpt-title">BidGPT</h2><p id="bidgpt-sub">Tender search for this website</p></div>' +
    '<button id="bidgpt-close" type="button" aria-label="Close tender chat">&times;</button></div>' +
    '<div id="bidgpt-status" role="status" aria-live="polite"><span class="bidgpt-dot bidgpt-pulse"></span><span id="bidgpt-status-text">Getting ready…</span></div>' +
    '<div id="bidgpt-log" role="log" aria-live="polite" aria-relevant="additions"></div>' +
    '<form id="bidgpt-form" action="javascript:void(0);" onsubmit="event.preventDefault(); return false;"><label class="bidgpt-sr" for="bidgpt-input">Ask about tenders</label>' +
    '<input id="bidgpt-input" type="text" maxlength="2000" autocomplete="off" placeholder="Search tenders (e.g. Karnataka electrical tenders)" disabled />' +
    '<button id="bidgpt-send" type="submit" disabled>Send</button></form>' +
    '</section>' +
    '<button id="bidgpt-launcher" type="button" aria-label="Open tender search chat" aria-expanded="false" aria-controls="bidgpt-panel">' +
    '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5Z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>' +
    "<span>Find tenders</span></button>";

  // The snippet may be placed in <head>, where document.body does not exist
  // yet. Mount as soon as the body is available instead of throwing.
  if (document.body) {
    document.body.appendChild(root);
  } else {
    document.addEventListener(
      "DOMContentLoaded",
      function () {
        if (!root.isConnected) document.body.appendChild(root);
      },
      { once: true }
    );
  }

  var panel = root.querySelector("#bidgpt-panel");
  var launcher = root.querySelector("#bidgpt-launcher");
  var closeButton = root.querySelector("#bidgpt-close");
  var statusEl = root.querySelector("#bidgpt-status");
  var statusText = root.querySelector("#bidgpt-status-text");
  var log = root.querySelector("#bidgpt-log");
  var form = root.querySelector("#bidgpt-form");
  var input = root.querySelector("#bidgpt-input");
  var send = root.querySelector("#bidgpt-send");

  function setStatus(text, kind) {
    statusText.textContent = text;
    statusEl.setAttribute("data-error", kind === "error" ? "true" : "false");
    var dot = statusEl.querySelector(".bidgpt-dot");
    if (dot) dot.classList.toggle("bidgpt-pulse", kind === "loading");
  }

  function setComposerEnabled(enabled) {
    input.disabled = !enabled;
    send.disabled = !enabled;
  }

  function scrollLog() {
    log.scrollTop = log.scrollHeight;
  }

  function appendMessage(role, text, withCardClass) {
    var el = document.createElement("div");
    el.className =
      "bidgpt-msg " + (role === "user" ? "bidgpt-user" : "bidgpt-bot") +
      (withCardClass ? " bidgpt-plain" : "");
    el.textContent = text;
    log.appendChild(el);
    scrollLog();
    return el;
  }

  function safeUrl(value) {
    if (typeof value !== "string") return null;
    try {
      var parsed = new URL(value, window.location.href);
      if (parsed.protocol === "http:" || parsed.protocol === "https:") return parsed.href;
    } catch (e) {}
    return null;
  }

  function escapeHtml(str) {
    if (str == null) return "";
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function renderTenderCard(tender) {
    var card = document.createElement("article");
    card.className = "bidgpt-card";

    // 1. Green Header Banner
    var header = document.createElement("div");
    header.className = "bidgpt-card-header";

    var headerLeft = document.createElement("div");
    headerLeft.className = "bidgpt-card-header-left";
    // Prefer state > location, treating sentinel "not listed on the page" as empty
    var stateVal = (tender.state && tender.state !== "not listed on the page") ? tender.state : null;
    var locVal   = (tender.location && tender.location !== "not listed on the page") ? tender.location : null;
    var stateName = stateVal || locVal || null;
    // Build a readable label: use specific city/district if available, else state
    var locationLabel = locVal || stateVal || null;
    headerLeft.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12 2C8.13 2 5 5.13 5 9c0 5.25 7 13 7 13s7-7.75 7-13c0-3.87-3.13-7-7-7zm0 9.5c-1.38 0-2.5-1.12-2.5-2.5s1.12-2.5 2.5-2.5 2.5 1.12 2.5 2.5-1.12 2.5-2.5 2.5z"/></svg><span>' +
      (locationLabel ? escapeHtml(locationLabel) : "Tenders") + "</span>";

    var headerRight = document.createElement("div");
    headerRight.className = "bidgpt-card-header-right";
    // Right label: show tender ID if real, else show tender type, else "Active"
    var refId = (tender.tender_id && tender.tender_id !== "not listed on the page")
      ? tender.tender_id
      : (tender.type && tender.type !== "not listed on the page")
      ? tender.type
      : "Active";
    headerRight.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="23 6 13.5 15.5 8.5 10.5 1 18"></polyline><polyline points="17 6 23 6 23 12"></polyline></svg><span>' +
      escapeHtml(refId) + "</span>";

    header.appendChild(headerLeft);
    header.appendChild(headerRight);
    card.appendChild(header);

    // 2. Card Body
    var body = document.createElement("div");
    body.className = "bidgpt-card-body";

    // Tender Info Line — detect and replace generic listing-page titles
    var info = document.createElement("p");
    info.className = "bidgpt-card-info";
    var infoLabel = document.createElement("span");
    infoLabel.className = "bidgpt-card-info-label";
    infoLabel.textContent = "Tender Info: ";
    info.appendChild(infoLabel);
    var rawTitle = tender.title || "not listed on the page";
    var lowerTitle = rawTitle.toLowerCase().trim();
    // Generic listing-page patterns to replace with more specific info
    var isGenericTitle = (
      rawTitle === "not listed on the page" ||
      lowerTitle.indexOf("find government") !== -1 ||
      lowerTitle.indexOf("etendering") !== -1 ||
      lowerTitle.indexOf("eprocurement") !== -1 ||
      lowerTitle.indexOf("all tenders") === 0 ||
      lowerTitle.endsWith(" tenders") ||
      lowerTitle === "tenders"
    );
    var displayTitle = rawTitle;
    if (isGenericTitle) {
      // Build a more specific display from structured fields
      var parts = [];
      if (tender.department && tender.department !== "not listed on the page") parts.push(tender.department);
      if (tender.products && tender.products !== "not listed on the page") parts.push(tender.products);
      if (tender.type && tender.type !== "not listed on the page" && parts.length === 0) parts.push(tender.type);
      if (locationLabel && parts.length === 0) parts.push(locationLabel);
      if (parts.length > 0) {
        displayTitle = parts.join(" — ");
      }
    }
    var infoText = document.createTextNode(displayTitle);
    info.appendChild(infoText);
    body.appendChild(info);

    // Closing Date Line
    var closingDate = (tender.bid_by_date && tender.bid_by_date !== "not listed on the page")
      ? tender.bid_by_date
      : "Refer Doc";
    var closingEl = document.createElement("div");
    closingEl.className = "bidgpt-card-closing";
    closingEl.innerHTML =
      '<div class="bidgpt-card-closing-badge">' +
      '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><polyline points="12 6 12 12 16 14"></polyline></svg>' +
      '<span>Closing Date: <strong class="bidgpt-card-closing-val">' + escapeHtml(closingDate) + '</strong></span>' +
      '</div>';
    body.appendChild(closingEl);

    // Stats Row: EMD, Exemption, ECV
    var emdVal = (tender.emd && tender.emd !== "not listed on the page")
      ? tender.emd
      : (tender.budget && tender.budget !== "not listed on the page")
      ? tender.budget
      : "Refer Doc";
    var exemptionVal = (tender.emd_exemption && tender.emd_exemption !== "not listed on the page")
      ? tender.emd_exemption
      : "Refer Doc";
    var ecvVal = (tender.ecv && tender.ecv !== "not listed on the page")
      ? tender.ecv
      : (tender.budget && tender.budget !== "not listed on the page")
      ? tender.budget
      : "Refer Doc";

    var metricsRow = document.createElement("div");
    metricsRow.className = "bidgpt-metrics-row";
    metricsRow.innerHTML =
      '<div class="bidgpt-metric-item"><span class="bidgpt-metric-lbl">EMD:</span> <span class="bidgpt-metric-val-emd">' + escapeHtml(emdVal) + '</span></div>' +
      '<div class="bidgpt-metric-item"><span class="bidgpt-metric-lbl">Exemption:</span> <span class="bidgpt-metric-val-exemption">' + escapeHtml(exemptionVal) + '</span></div>' +
      '<div class="bidgpt-metric-item"><span class="bidgpt-metric-lbl">ECV:</span> <span class="bidgpt-metric-val-ecv">' + escapeHtml(ecvVal) + '</span></div>';
    body.appendChild(metricsRow);

    // Actions Row (Social share + View Tender)
    var actionsRow = document.createElement("div");
    actionsRow.className = "bidgpt-card-actions";

    var shareIcons = document.createElement("div");
    shareIcons.className = "bidgpt-share-icons";

    var shareUrl = safeUrl(tender.source || tender.url) || window.location.href;
    var shareTitle = tender.title || "Tender Details";

    var socials = [
      { name: "Facebook", cls: "bidgpt-share-fb", url: "https://www.facebook.com/sharer/sharer.php?u=" + encodeURIComponent(shareUrl), svg: '<svg viewBox="0 0 24 24"><path d="M18 2h-3a5 5 0 0 0-5 5v3H7v4h3v8h4v-8h3l1-4h-4V7a1 1 0 0 1 1-1h3z"/></svg>' },
      { name: "Twitter", cls: "bidgpt-share-tw", url: "https://twitter.com/intent/tweet?text=" + encodeURIComponent(shareTitle) + "&url=" + encodeURIComponent(shareUrl), svg: '<svg viewBox="0 0 24 24"><path d="M23 3a10.9 10.9 0 0 1-3.14 1.53 4.48 4.48 0 0 0-7.86 3v1A10.66 10.66 0 0 1 3 4s-4 9 5 13a11.64 11.64 0 0 1-7 2c9 5 20 0 20-11.5a4.5 4.5 0 0 0-.08-.83A7.72 7.72 0 0 0 23 3z"/></svg>' },
      { name: "LinkedIn", cls: "bidgpt-share-in", url: "https://www.linkedin.com/sharing/share-offsite/?url=" + encodeURIComponent(shareUrl), svg: '<svg viewBox="0 0 24 24"><path d="M16 8a6 6 0 0 1 6 6v7h-4v-7a2 2 0 0 0-2-2 2 2 0 0 0-2 2v7h-4v-7a6 6 0 0 1 6-6z"/><rect x="2" y="9" width="4" height="12"/><circle cx="4" cy="4" r="2"/></svg>' },
      { name: "WhatsApp", cls: "bidgpt-share-wa", url: "https://api.whatsapp.com/send?text=" + encodeURIComponent(shareTitle + " " + shareUrl), svg: '<svg viewBox="0 0 24 24"><path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5Z"/></svg>' }
    ];

    socials.forEach(function (s) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "bidgpt-share-btn " + s.cls;
      btn.setAttribute("aria-label", "Share on " + s.name);
      btn.innerHTML = s.svg;
      btn.addEventListener("click", function (e) {
        e.preventDefault();
        window.open(s.url, "_blank", "noopener,noreferrer");
      });
      shareIcons.appendChild(btn);
    });
    actionsRow.appendChild(shareIcons);

    // View Tender Link (opens direct official website page in new tab)
    var sourceUrl = safeUrl(tender.source || tender.url);
    var viewBtn = document.createElement("a");
    viewBtn.className = "bidgpt-view-btn bidgpt-source";
    viewBtn.href = sourceUrl || "#";
    viewBtn.target = "_blank";
    viewBtn.rel = "noopener noreferrer";
    viewBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline><line x1="16" y1="13" x2="8" y2="13"></line><line x1="16" y1="17" x2="8" y2="17"></line><polyline points="10 9 9 9 8 9"></polyline></svg><span>View Tender</span>';
    viewBtn.setAttribute("aria-label", "Open official tender page for " + (tender.title || "tender"));
    viewBtn.addEventListener("click", function (e) {
      if (!sourceUrl || sourceUrl === "#") {
        e.preventDefault();
      }
    });
    actionsRow.appendChild(viewBtn);

    body.appendChild(actionsRow);
    card.appendChild(body);

    // Clicking anywhere on card opens the official tender website directly
    if (sourceUrl && sourceUrl !== "#") {
      card.style.cursor = "pointer";
      card.addEventListener("click", function (e) {
        if (e.target.closest("a, button")) return;
        window.open(sourceUrl, "_blank", "noopener,noreferrer");
      });
    }

    return card;
  }

  function renderResponse(data) {
    var answer = typeof data.answer === "string" ? data.answer : "";
    if (answer) {
      appendMessage("assistant", answer, !(data.tenders && data.tenders.length));
      state.history.push({ role: "assistant", content: answer });
    }
    if (Array.isArray(data.tenders) && data.tenders.length) {
      var allTenders = data.tenders;
      var total = allTenders.length;
      var pageSize = 10;
      var currentPage = 1;
      var currentFilter = "ALL";

      var container = document.createElement("div");
      container.className = "bidgpt-cards-container";

      var intent = data.intent || {};
      var isAllTenders = Boolean(intent.all_tenders || (!intent.location && !intent.type && !intent.keyword));
      var hasTypeIntent = Boolean(intent.type && (!intent.location || intent.generic_tender));
      var hasLocIntent = Boolean(intent.location && !intent.type);

      var catMap = {};
      var locMap = {};
      allTenders.forEach(function (t) {
        // Trade category
        var cat = (t.type && t.type !== "not listed on the page") ? t.type.trim() : "Other";
        if (cat.indexOf(" - ") !== -1) cat = cat.split(" - ")[0].trim();
        if (cat.length > 20) cat = cat.substring(0, 18).trim() + "…";
        t.__clean_cat = cat;
        catMap[cat] = (catMap[cat] || 0) + 1;

        // Location / State
        var loc = "";
        if (t.state && t.state !== "not listed on the page") {
          loc = t.state.trim();
        } else if (t.location && t.location !== "not listed on the page") {
          var locParts = t.location.split(",");
          loc = locParts[locParts.length - 1].trim();
        }
        if (!loc || loc === "not listed on the page" || loc.length > 25) loc = "Other";
        t.__clean_loc = loc;
        locMap[loc] = (locMap[loc] || 0) + 1;
      });

      var distinctCats = Object.keys(catMap);
      var distinctLocs = Object.keys(locMap);

      var selectedCat = "ALL";
      var selectedLoc = "ALL";
      var primaryFilter = null; // "cat" or "loc"
      var typeRowObj = null;
      var locRowObj = null;
      var isDualFilter = Boolean(isAllTenders || (distinctCats.length > 1 && distinctLocs.length > 1 && !intent.location && !intent.type));

      // Master category and location maps
      var masterCatMap = {};
      var masterLocMap = {};
      allTenders.forEach(function (t) {
        masterCatMap[t.__clean_cat] = (masterCatMap[t.__clean_cat] || 0) + 1;
        masterLocMap[t.__clean_loc] = (masterLocMap[t.__clean_loc] || 0) + 1;
      });

      var masterCats = Object.keys(masterCatMap).sort(function (a, b) {
        return masterCatMap[b] - masterCatMap[a];
      });
      var masterLocs = Object.keys(masterLocMap).sort(function (a, b) {
        return masterLocMap[b] - masterLocMap[a];
      });

      function renderChipsIntoWrap(chipsWrap, keys, counts, selectedVal, onSelect) {
        chipsWrap.innerHTML = "";
        var activeBtn = null;
        keys.forEach(function (key) {
          var btn = document.createElement("button");
          btn.type = "button";
          var isAct = (key === selectedVal);
          btn.className = "bidgpt-chip" + (isAct ? " active" : "");
          btn.textContent = (key === "ALL" ? "All" : key) + " (" + (counts[key] || 0) + ")";
          btn.addEventListener("click", function () {
            onSelect(key);
          });
          chipsWrap.appendChild(btn);
          if (isAct) activeBtn = btn;
        });
        if (activeBtn && selectedVal !== "ALL") {
          try {
            activeBtn.scrollIntoView({ behavior: "smooth", inline: "nearest", block: "nearest" });
          } catch (_) {}
        }
      }

      function renderTypeRow() {
        if (!typeRowObj) return;

        // If user has a location selected, show only categories available in that location
        var matchingTenders = (isDualFilter && selectedLoc !== "ALL")
          ? allTenders.filter(function (t) { return t.__clean_loc === selectedLoc; })
          : allTenders;

        var counts = { ALL: matchingTenders.length };
        var dist = {};
        matchingTenders.forEach(function (t) {
          counts[t.__clean_cat] = (counts[t.__clean_cat] || 0) + 1;
          dist[t.__clean_cat] = true;
        });

        var catsToShow = (isDualFilter && selectedLoc !== "ALL")
          ? Object.keys(dist).sort(function (a, b) { return counts[b] - counts[a]; })
          : (isDualFilter ? masterCats : distinctCats.sort(function (a, b) { return catMap[b] - catMap[a]; }));

        if (selectedCat !== "ALL" && !dist[selectedCat]) {
          selectedCat = "ALL";
        }

        var keys = ["ALL"].concat(catsToShow);
        renderChipsIntoWrap(typeRowObj.wrap, keys, counts, selectedCat, function (key) {
          selectedCat = key;
          currentPage = 1;
          renderTypeRow();
          if (isDualFilter) renderLocRow();
          renderPage();
        });
      }

      function renderLocRow() {
        if (!locRowObj) return;

        // If user has a category selected, show only locations available in that category
        var matchingTenders = (isDualFilter && selectedCat !== "ALL")
          ? allTenders.filter(function (t) { return t.__clean_cat === selectedCat; })
          : allTenders;

        var counts = { ALL: matchingTenders.length };
        var dist = {};
        matchingTenders.forEach(function (t) {
          counts[t.__clean_loc] = (counts[t.__clean_loc] || 0) + 1;
          dist[t.__clean_loc] = true;
        });

        var locsToShow = (isDualFilter && selectedCat !== "ALL")
          ? Object.keys(dist).sort(function (a, b) { return counts[b] - counts[a]; })
          : (isDualFilter ? masterLocs : distinctLocs.sort(function (a, b) { return locMap[b] - locMap[a]; }));

        if (selectedLoc !== "ALL" && !dist[selectedLoc]) {
          selectedLoc = "ALL";
        }

        var keys = ["ALL"].concat(locsToShow);
        renderChipsIntoWrap(locRowObj.wrap, keys, counts, selectedLoc, function (key) {
          selectedLoc = key;
          currentPage = 1;
          if (isDualFilter) renderTypeRow();
          renderLocRow();
          renderPage();
        });
      }

      function createChipRow(title, iconSvg, ariaLabel) {
        var row = document.createElement("div");
        row.className = "bidgpt-filter-row";

        if (title) {
          var header = document.createElement("div");
          header.className = "bidgpt-filter-row-header";
          header.innerHTML = '<span class="bidgpt-filter-row-title">' + iconSvg + ' ' + escapeHtml(title) + '</span>';
          row.appendChild(header);
        }

        var chipsOuter = document.createElement("div");
        chipsOuter.className = "bidgpt-chips-scroll-wrap";

        var chipPrev = document.createElement("button");
        chipPrev.type = "button";
        chipPrev.className = "bidgpt-chip-nav-btn bidgpt-chip-prev";
        chipPrev.setAttribute("aria-label", "Scroll " + ariaLabel + " left");
        chipPrev.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"></polyline></svg>';

        var chipsWrap = document.createElement("div");
        chipsWrap.className = "bidgpt-filter-chips";

        var chipNext = document.createElement("button");
        chipNext.type = "button";
        chipNext.className = "bidgpt-chip-nav-btn bidgpt-chip-next";
        chipNext.setAttribute("aria-label", "Scroll " + ariaLabel + " right");
        chipNext.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"></polyline></svg>';

        chipPrev.addEventListener("click", function () {
          chipsWrap.scrollBy({ left: -140, behavior: "smooth" });
        });
        chipNext.addEventListener("click", function () {
          chipsWrap.scrollBy({ left: 140, behavior: "smooth" });
        });

        chipsOuter.appendChild(chipPrev);
        chipsOuter.appendChild(chipsWrap);
        chipsOuter.appendChild(chipNext);
        row.appendChild(chipsOuter);
        return { row: row, wrap: chipsWrap };
      }

      if (isDualFilter) {
        // Dual filter options: 1 is for type and 2 is for location
        var dualWrap = document.createElement("div");
        dualWrap.className = "bidgpt-dual-filter-container";

        var typeIcon = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z"></path><line x1="7" y1="7" x2="7.01" y2="7"></line></svg>';
        typeRowObj = createChipRow("1. Filter by Type", typeIcon, "categories");
        dualWrap.appendChild(typeRowObj.row);

        var locIcon = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"></path><circle cx="12" cy="10" r="3"></circle></svg>';
        locRowObj = createChipRow("2. Filter by Location", locIcon, "locations");
        dualWrap.appendChild(locRowObj.row);

        container.appendChild(dualWrap);

        renderTypeRow();
        renderLocRow();
      } else if (hasTypeIntent && distinctLocs.length > 1) {
        locRowObj = createChipRow("", "", "locations");
        container.appendChild(locRowObj.row);
        renderLocRow();
      } else if (distinctCats.length > 1) {
        typeRowObj = createChipRow("", "", "categories");
        container.appendChild(typeRowObj.row);
        renderTypeRow();
      }

      // Helper to create synchronized pagination bar
      function createPaginationBar() {
        var bar = document.createElement("div");
        bar.className = "bidgpt-pagination-bar";
        var prev = document.createElement("button");
        prev.type = "button";
        prev.className = "bidgpt-page-btn bidgpt-prev-btn";
        prev.innerHTML = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"></polyline></svg><span>Prev</span>';

        var info = document.createElement("span");
        info.className = "bidgpt-page-info";

        var next = document.createElement("button");
        next.type = "button";
        next.className = "bidgpt-page-btn bidgpt-next-btn";
        next.innerHTML = '<span>Next</span><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"></polyline></svg>';

        prev.addEventListener("click", function () {
          if (currentPage > 1) {
            currentPage--;
            renderPage();
            container.scrollIntoView({ behavior: "smooth", block: "start" });
          }
        });

        next.addEventListener("click", function () {
          var activeList = getActiveList();
          var maxPages = Math.ceil(activeList.length / pageSize);
          if (currentPage < maxPages) {
            currentPage++;
            renderPage();
            container.scrollIntoView({ behavior: "smooth", block: "start" });
          }
        });

        bar.appendChild(prev);
        bar.appendChild(info);
        bar.appendChild(next);
        return { bar: bar, prev: prev, next: next, info: info };
      }

      var topPaging = createPaginationBar();
      topPaging.bar.classList.add("bidgpt-top-paging");
      container.appendChild(topPaging.bar);

      // Card list container
      var cardsList = document.createElement("div");
      cardsList.className = "bidgpt-cards-list";
      container.appendChild(cardsList);

      var bottomPaging = createPaginationBar();
      bottomPaging.bar.classList.add("bidgpt-bottom-paging");
      container.appendChild(bottomPaging.bar);

      function getActiveList() {
        if (selectedCat === "ALL" && selectedLoc === "ALL") return allTenders;
        return allTenders.filter(function (t) {
          var matchCat = (selectedCat === "ALL" || t.__clean_cat === selectedCat);
          var matchLoc = (selectedLoc === "ALL" || t.__clean_loc === selectedLoc);
          return matchCat && matchLoc;
        });
      }

      function updatePaginationUI(pUI, currentPage, maxPages, startIdx, endIdx, totalInCat) {
        if (totalInCat > pageSize) {
          pUI.bar.style.display = "flex";
          pUI.prev.disabled = currentPage <= 1;
          pUI.next.disabled = currentPage >= maxPages;
          pUI.info.innerHTML =
            'Showing <strong>' + (startIdx + 1) + '–' + endIdx + '</strong> of <strong>' + totalInCat + '</strong> ' +
            '<span class="bidgpt-page-range">(Page ' + currentPage + '/' + maxPages + ')</span>';
        } else {
          pUI.bar.style.display = "none";
        }
      }

      function renderPage() {
        var list = getActiveList();
        var totalInCat = list.length;
        var maxPages = Math.max(1, Math.ceil(totalInCat / pageSize));
        if (currentPage > maxPages) currentPage = maxPages;

        var startIdx = (currentPage - 1) * pageSize;
        var endIdx = Math.min(startIdx + pageSize, totalInCat);
        var pageItems = list.slice(startIdx, endIdx);

        cardsList.innerHTML = "";
        if (pageItems.length === 0) {
          var emptyDiv = document.createElement("div");
          emptyDiv.className = "bidgpt-empty-combo";
          emptyDiv.innerHTML =
            "<p>No tenders found matching <strong>" +
            (selectedCat !== "ALL" ? escapeHtml(selectedCat) : "All Types") + "</strong> in <strong>" +
            (selectedLoc !== "ALL" ? escapeHtml(selectedLoc) : "All Locations") + "</strong>.</p>" +
            '<button type="button" class="bidgpt-reset-filter-btn">Reset Filters</button>';
          emptyDiv.querySelector(".bidgpt-reset-filter-btn").addEventListener("click", function () {
            selectedCat = "ALL";
            selectedLoc = "ALL";
            primaryFilter = null;
            if (isDualFilter) {
              renderTypeRow();
              renderLocRow();
            } else if (typeRowObj) {
              renderTypeRow();
            } else if (locRowObj) {
              renderLocRow();
            }
            currentPage = 1;
            renderPage();
          });
          cardsList.appendChild(emptyDiv);
        } else {
          pageItems.forEach(function (tender) {
            var card = renderTenderCard(tender);
            card.classList.add("bidgpt-card-fadein");
            cardsList.appendChild(card);
          });
        }

        // Update top & bottom pagination bars in sync
        updatePaginationUI(topPaging, currentPage, maxPages, startIdx, endIdx, totalInCat);
        updatePaginationUI(bottomPaging, currentPage, maxPages, startIdx, endIdx, totalInCat);
      }

      // Initial render
      renderPage();
      log.appendChild(container);
    }
    if (state.history.length > 16) {
      state.history = state.history.slice(-16);
    }
    scrollLog();
  }

  function parseError(response) {
    return response
      .json()
      .then(function (data) {
        return (data && (data.detail || data.answer)) || "Request failed";
      })
      .catch(function () {
        return "Request failed (" + response.status + ")";
      });
  }

  function fetchStatus() {
    function tryStatus(endpoint) {
      return fetch(endpoint, {
        method: "GET",
        mode: "cors",
        credentials: "omit",
        headers: { Accept: "application/json" }
      }).then(function (response) {
        if (!response.ok) {
          return parseError(response).then(function (message) {
            throw new Error(message);
          });
        }
        return response.json();
      });
    }

    return tryStatus(apiBase + "/api/sites/" + encodeURIComponent(siteKey) + "/status")
      .catch(function () {
        return tryStatus(apiBase + "/sites/" + encodeURIComponent(siteKey) + "/status");
      });
  }

  function applyStatus(data) {
    state.status = data;
    var hasIndex = !!data.has_index;
    var status = data.status;
    if (status === "ready" || hasIndex) {
      state.ready = true;
      setComposerEnabled(true);
      if (status === "crawling" || status === "queued") {
        setStatus("Updating index — answers use the last ready crawl.", "ok");
      } else {
        var count = data.page_count || data.pages_found || 0;
        setStatus(count + (count === 1 ? " page indexed" : " pages indexed"), "ok");
      }
      if (!state.greeted) {
        state.greeted = true;
        appendMessage(
          "assistant",
          "Hi! Which type of tender and which location are you looking for?",
          true
        );
      }
      stopPolling();
      return;
    }
    state.ready = false;
    setComposerEnabled(false);
    if (status === "failed") {
      setStatus(
        data.last_error
          ? "Index build failed: " + data.last_error
          : "Index build failed. Please try again later.",
        "error"
      );
      stopPolling();
      return;
    }
    var pages = data.pages_found || 0;
    setStatus(
      pages > 0 ? "Getting ready — crawled " + pages + " pages…" : "Getting ready…",
      "loading"
    );
    startPolling();
  }

  function refreshStatus() {
    fetchStatus()
      .then(applyStatus)
      .catch(function (error) {
        state.ready = false;
        setComposerEnabled(false);
        setStatus(error.message || "Could not reach BidGPT.", "error");
      });
  }

  function startPolling() {
    if (state.pollTimer) return;
    state.pollTimer = window.setInterval(refreshStatus, 3000);
  }

  function stopPolling() {
    if (state.pollTimer) {
      window.clearInterval(state.pollTimer);
      state.pollTimer = null;
    }
  }

  function openPanel() {
    state.open = true;
    panel.setAttribute("data-open", "true");
    launcher.setAttribute("aria-expanded", "true");
    launcher.style.display = "none";
    refreshStatus();
    window.setTimeout(function () {
      if (!input.disabled) input.focus();
      else closeButton.focus();
    }, 30);
  }

  function closePanel() {
    state.open = false;
    panel.setAttribute("data-open", "false");
    launcher.setAttribute("aria-expanded", "false");
    launcher.style.display = "";
    stopPolling();
    launcher.focus();
  }

  launcher.addEventListener("click", openPanel);
  closeButton.addEventListener("click", closePanel);
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") {
      if (state.open) closePanel();
    }
  });

  function handleSubmit(event) {
    if (event) {
      event.preventDefault();
      event.stopPropagation();
    }
    if (!state.ready || state.loading) return false;
    var message = input.value.trim();
    if (!message) return false;
    input.value = "";
    state.loading = true;
    setComposerEnabled(false);
    appendMessage("user", message, false);
    state.history.push({ role: "user", content: message });
    setStatus("Searching indexed pages…", "loading");

    var chatPayload = JSON.stringify({
      site_key: siteKey,
      message: message,
      history: state.history.slice(-10)
    });

    function trySendChat(endpoint) {
      return fetch(endpoint, {
        method: "POST",
        mode: "cors",
        credentials: "omit",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: chatPayload
      }).then(function (response) {
        if (!response.ok) {
          return parseError(response).then(function (detail) {
            throw new Error(detail);
          });
        }
        return response.json();
      });
    }

    trySendChat(apiBase + "/api/chat")
      .catch(function () { return trySendChat(apiBase + "/chat"); })
      .catch(function () { return trySendChat(apiBase + "/"); })
      .then(function (data) {
        renderResponse(data);
        applyStatus({
          status: data.status || "ready",
          has_index: true,
          page_count: (state.status && state.status.page_count) || 0
        });
      })
      .catch(function (error) {
        appendMessage(
          "assistant",
          error.message || "Sorry, I could not answer that. Please try again.",
          true
        );
        setStatus("Could not complete the search.", "error");
      })
      .finally(function () {
        state.loading = false;
        setComposerEnabled(true);
        input.focus();
      });
    return false;
  }

  form.addEventListener("submit", handleSubmit);
  send.addEventListener("click", function (event) {
    handleSubmit(event);
  });

  // Defer the first status check until the widget is actually opened so a page
  // with the snippet does not poll forever in a background tab.
  launcher.addEventListener(
    "mouseenter",
    function () {
      if (!state.status) refreshStatus();
    },
    { once: true }
  );
})();
