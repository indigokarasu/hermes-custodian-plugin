/**
 * Custodian Dashboard Plugin — real Hermes dashboard plugin (SDK / React IIFE).
 *
 * Renders live data from /api/plugins/custodian/status:
 *   health{state,label,detail}, issues{open,escalated,resolved_today},
 *   escalations[] (real issue objects), system{cron,skills,gateway},
 *   scan_history[], last_scan{at,age_minutes}.
 *
 * Theme-native: dashboard tokens (var(--color-*)) + Tailwind classes + SDK components.
 * Principles: grid-first, quiet-by-default, escalation-first, real actions, no fabricated data.
 */
(function () {
  "use strict";

  var SDK = window.__HERMES_PLUGIN_SDK__;
  var PLUGINS = window.__HERMES_PLUGINS__;
  if (!SDK || !PLUGINS) { console.error("[custodian] Hermes plugin SDK not available."); return; }

  var React = SDK.React;
  var h = React.createElement;
  var useState = SDK.hooks.useState;
  var useEffect = SDK.hooks.useEffect;
  var useCallback = SDK.hooks.useCallback;
  var fetchJSON = SDK.fetchJSON;
  var C = SDK.components;
  var Card = C.Card, CardHeader = C.CardHeader, CardTitle = C.CardTitle, CardContent = C.CardContent;
  var Badge = C.Badge, Button = C.Button;
  var Spinner = C.Spinner || function (p) { return h("span", { className: (p.className || "") + " animate-pulse" }, "…"); };
  var cn = (SDK.utils && SDK.utils.cn) || function () { return Array.prototype.filter.call(arguments, Boolean).join(" "); };

  // --- one-time scoped CSS injection (avoids manifest css dependency / restart) ---
  function injectCSS() {
    if (document.getElementById("cstd-css")) return;
    var s = document.createElement("style");
    s.id = "cstd-css";
    s.textContent = [
      // Hermes mockup skin, scoped to the plugin. Overriding the shadcn --color-* tokens
      // here re-skins the SDK Card/Badge/Button (custom props inherit) to the purple palette.
      ".cstd{--panel:rgba(255,255,255,.025);--panel2:rgba(255,255,255,.05);--bd:rgba(150,130,230,.18);--bd2:rgba(150,130,230,.30);--title:#cdc6f5;--tx:#e7e5f1;--muted:#9b97b8;--accent:#a78bfa;--ok:#4fd6a6;--warn:#f0b54e;--danger:#f0706e;--info:#6aa6f2;--mono:ui-monospace,'SF Mono',Menlo,Consolas,monospace;--color-background:#0a0a14;--color-foreground:#e7e5f1;--color-card:#0e0e1a;--color-card-foreground:#e7e5f1;--color-popover:#12121f;--color-popover-foreground:#e7e5f1;--color-border:rgba(150,130,230,.18);--color-input:rgba(150,130,230,.22);--color-muted:#15151f;--color-muted-foreground:#9b97b8;--color-primary:#a78bfa;--color-primary-foreground:#0a0a14;--color-secondary:#17151f;--color-secondary-foreground:#cdc6f5;--color-accent:#1c1830;--color-accent-foreground:#cdc6f5;--color-destructive:#f0706e;--color-destructive-foreground:#0a0a14;--color-ring:#a78bfa;display:flex;flex-direction:column;gap:1rem;color:var(--tx)}",
      ".cstd .text-muted-foreground{color:var(--muted)}",
      ".cstd .text-green-500{color:var(--ok)}.cstd .text-yellow-500{color:var(--warn)}.cstd .text-destructive{color:var(--danger)}",
      // SDK cards -> panel + purple hairline + mono-ish titles
      ".cstd [data-slot=card],.cstd .rounded-xl,.cstd .rounded-lg{background:var(--panel)!important;border-color:var(--bd)!important;border-radius:11px!important}",
      ".cstd [data-slot=card-title]{font-family:var(--mono);font-size:.72rem!important;letter-spacing:.13em;text-transform:uppercase;color:var(--muted)!important;font-weight:500}",
      ".cstd-head{display:flex;align-items:flex-start;justify-content:space-between;gap:1rem;flex-wrap:wrap;padding:2px}",
      ".cstd-actions{display:flex;gap:.5rem;flex:0 0 auto;align-self:flex-start}",
      ".cstd-kpis{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:.75rem}",
      ".cstd-kpi{background:var(--panel);border:1px solid var(--bd);border-radius:11px;padding:14px 15px}",
      ".cstd-kpi-l{font-family:var(--mono);font-size:.69rem;letter-spacing:.12em;text-transform:uppercase;color:var(--muted);min-height:2.5em;line-height:1.35}",
      ".cstd-kpi-v{font-size:1.7rem;font-weight:300;line-height:1;margin-top:.4rem;color:var(--title)}",
      ".cstd-grid{display:grid;grid-template-columns:2fr 1fr;gap:1rem;align-items:start}",
      ".cstd-row{display:flex;align-items:baseline;justify-content:space-between;gap:.5rem;font-size:.82rem;font-family:var(--mono);padding:.2rem 0;border-top:1px solid var(--bd)}",
      ".cstd-row:first-child{border-top:none}",
      ".cstd-esc{border-left:2px solid var(--bd)!important}",
      ".cstd-esc.t4{border-left-color:var(--danger)!important}",
      ".cstd-esc.t3{border-left-color:var(--warn)!important}",
      ".cstd-tick{display:flex;align-items:center;gap:.5rem;font-size:.78rem;font-family:var(--mono);padding:.18rem 0}",
      ".cstd-dot{width:.5rem;height:.5rem;border-radius:9999px;flex:0 0 auto}",
      "@media(max-width:900px){.cstd-grid{grid-template-columns:1fr}}",
      "@media(max-width:600px){.cstd-kpis{grid-template-columns:1fr}}",
    ].join("");
    document.head.appendChild(s);
  }

  function relTime(iso) {
    if (!iso) return "never";
    try {
      var diff = Date.now() - new Date(iso).getTime();
      var m = Math.floor(diff / 60000);
      if (m < 1) return "just now";
      if (m < 60) return m + "m ago";
      var hr = Math.floor(m / 60);
      if (hr < 24) return hr + "h ago";
      return Math.floor(hr / 24) + "d ago";
    } catch (e) { return iso; }
  }
  function clockTime(iso) {
    if (!iso) return "";
    try { return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }); }
    catch (e) { return ""; }
  }
  function tierTone(t) { return t >= 4 ? "destructive" : t === 3 ? "warning" : t === 2 ? "outline" : "success"; }
  function tierLabel(t) { return ({ 1: "T1 · auto-fix", 2: "T2 · plan", 3: "T3 · escalate", 4: "T4 · critical" })[t] || ("T" + t); }

  // --- KPI tile ---
  function Kpi(label, value, color) {
    return h("div", { className: "cstd-kpi" },
      h("div", { className: "cstd-kpi-l" }, label),
      h("div", { className: "cstd-kpi-v", style: color ? { color: color } : null }, value)
    );
  }

  // --- Escalation card (real issue object) ---
  function EscalationCard(issue, i) {
    var tier = issue.tier || 3;
    var title = issue.title || issue.description || issue.fingerprint || issue.issue_id || "Untitled issue";
    var jobs = issue.affected_jobs || [];
    return h(Card, { key: i, className: cn("cstd-esc", "t" + tier) },
      h(CardContent, { className: "py-3" },
        h("div", { className: "flex flex-col gap-2" },
          h("div", { className: "flex items-center gap-2 flex-wrap" },
            h(Badge, { tone: tierTone(tier) }, tierLabel(tier)),
            h("span", { className: "text-sm font-medium" }, title)
          ),
          issue.description && issue.description !== title
            ? h("span", { className: "text-xs text-muted-foreground" },
                issue.description.length > 240 ? issue.description.slice(0, 240) + "…" : issue.description)
            : null,
          jobs.length
            ? h("span", { className: "text-xs text-muted-foreground" }, "Affects: " + jobs.join(", "))
            : null,
          issue.recommendation
            ? h("span", { className: "text-xs text-muted-foreground italic" }, "Recommended: " + issue.recommendation)
            : null,
          h("div", { className: "flex items-center gap-2 pt-1" },
            issue.source ? h("span", { className: "text-xs text-muted-foreground" }, "source: " + issue.source) : null,
            h("span", { className: "text-xs text-muted-foreground" }, "· seen " + relTime(issue.last_seen || issue.created_at))
          )
        )
      )
    );
  }

  function CustodianDashboard() {
    var st = useState(null), data = st[0], setData = st[1];
    var ls = useState(true), loading = ls[0], setLoading = ls[1];
    var es = useState(null), err = es[0], setErr = es[1];
    var bs = useState(false), busy = bs[0], setBusy = bs[1];

    var load = useCallback(function () {
      fetchJSON("/api/plugins/custodian/status")
        .then(function (r) { setData(r); setErr(null); setLoading(false); })
        .catch(function (e) { setErr((e && e.message) || "Failed to load"); setLoading(false); });
    }, []);

    useEffect(function () { injectCSS(); load(); var iv = setInterval(load, 60000); return function () { clearInterval(iv); }; }, [load]);

    var scan = useCallback(function (mode) {
      setBusy(true);
      fetchJSON("/api/plugins/custodian/scan?mode=" + mode, { method: "POST" })
        .then(function () { setTimeout(load, 1200); })
        .catch(function (e) { setErr("Scan failed: " + ((e && e.message) || "error")); })
        .finally(function () { setBusy(false); });
    }, [load]);

    if (loading && !data) {
      return h("div", { className: "flex items-center gap-2 p-8 text-sm text-muted-foreground" },
        h(Spinner, { className: "h-4 w-4" }), "Loading Custodian…");
    }
    if (err && !data) {
      return h("div", { className: "p-4 text-sm text-destructive", role: "alert" },
        "Error: " + err, h(Button, { size: "sm", variant: "outline", className: "ml-2", onClick: load }, "Retry"));
    }
    if (!data) return null;

    var health = data.health || {};
    var issues = data.issues || {};
    var system = data.system || {};
    var escalations = data.escalations || [];
    var scans = data.scan_history || [];
    var lastScan = data.last_scan || {};
    var clear = health.state === "clear";

    var actions = h("div", { className: "cstd-actions" },
      h(Button, { size: "sm", variant: "outline", disabled: busy, onClick: function () { scan("light"); } }, busy ? "Scanning…" : "Light scan"),
      h(Button, { size: "sm", variant: "outline", disabled: busy, onClick: function () { scan("deep"); } }, "Deep scan")
    );

    // Header: single health source + actions (top-aligned)
    var head = h("div", { className: "cstd-head" },
      h("div", { className: "flex items-start gap-3" },
        h("span", { className: cn("text-2xl leading-none mt-0.5", clear ? "text-green-500" : "text-yellow-500") }, clear ? "✓" : "⚠"),
        h("div", { className: "flex flex-col" },
          h("span", { className: "text-base font-medium" }, health.label || (clear ? "All clear" : "Needs attention")),
          health.detail ? h("span", { className: "text-sm text-muted-foreground" }, health.detail) : null,
          h("span", { className: "text-xs text-muted-foreground mt-1" }, "Last scan " + (lastScan.at ? relTime(lastScan.at) : "—"))
        )
      ),
      actions
    );

    var kpis = h("div", { className: "cstd-kpis" },
      Kpi("Open issues", issues.open || 0, (issues.open ? "var(--color-warning,#f0b54e)" : null)),
      Kpi("Escalated", issues.escalated || 0, (issues.escalated ? "var(--color-destructive,#f0706e)" : null)),
      Kpi("Resolved today", issues.resolved_today || 0, (issues.resolved_today ? "var(--color-green-500,#4fd6a6)" : null))
    );

    // System card
    var systemCard = h(Card, null,
      h(CardHeader, { className: "pb-2" }, h(CardTitle, { className: "text-sm" }, "System")),
      h(CardContent, null,
        h("div", { className: "flex flex-col" },
          h("div", { className: "cstd-row" }, h("span", { className: "text-muted-foreground" }, "Cron"),
            h("span", null, (system.cron && system.cron.total != null ? system.cron.total : "—") + " jobs"
              + (system.cron && system.cron.disabled ? " · " + system.cron.disabled + " disabled" : ""))),
          h("div", { className: "cstd-row" }, h("span", { className: "text-muted-foreground" }, "Skills"),
            h("span", null, (system.skills && system.skills.active != null ? system.skills.active : "—") + " active"
              + (system.skills && system.skills.stale ? " · " + system.skills.stale + " stale" : ""))),
          h("div", { className: "cstd-row" }, h("span", { className: "text-muted-foreground" }, "Gateway"),
            h("span", null, "uptime " + ((system.gateway && system.gateway.uptime) || "—")))
        )
      )
    );

    // Scan history card
    var historyCard = h(Card, null,
      h(CardHeader, { className: "pb-2" }, h(CardTitle, { className: "text-sm" }, "Recent scans")),
      h(CardContent, null,
        scans.length === 0
          ? h("span", { className: "text-xs text-muted-foreground" }, "No scans recorded yet.")
          : h("div", { className: "flex flex-col" }, scans.slice(0, 6).map(function (s, i) {
              var hasFix = s.fixes_applied > 0, hasIssue = s.issues_found > 0;
              return h("div", { key: i, className: "cstd-tick" },
                h("span", { className: "text-muted-foreground", style: { width: "3.5rem" } }, clockTime(s.created_at) || (s.created_at || "").slice(5, 10)),
                h("span", { className: "cstd-dot", style: { background: hasFix ? "var(--color-green-500,#4fd6a6)" : hasIssue ? "var(--color-warning,#f0b54e)" : "var(--color-muted-foreground)" } }),
                h("span", null, hasIssue ? (s.issues_found + " issue" + (s.issues_found !== 1 ? "s" : "")) : "clean"),
                hasFix ? h("span", { className: "text-green-500" }, "· " + s.fixes_applied + " fixed") : null,
                s.has_escalations ? h("span", { className: "text-yellow-500" }, "· escalated") : null
              );
            }))
      )
    );

    // Escalations OR all-clear (escalation-first)
    var attentionBlock;
    if (escalations.length) {
      attentionBlock = h("div", { className: "flex flex-col gap-2" },
        h("span", { className: "text-sm font-medium" }, "Needs your attention (" + escalations.length + ")"),
        escalations.map(EscalationCard)
      );
    } else {
      attentionBlock = h(Card, null, h(CardContent, { className: "py-6" },
        h("div", { className: "flex flex-col items-center gap-1 text-center" },
          h("span", { className: "text-green-500 text-2xl" }, "✓"),
          h("span", { className: "text-sm font-medium" }, "Nothing needs you"),
          h("span", { className: "text-xs text-muted-foreground" },
            "Custodian is handling things autonomously"
            + (issues.resolved_today ? " · " + issues.resolved_today + " resolved today" : "") + ".")
        )
      ));
    }

    return h("div", { className: "cstd p-4" },
      head,
      kpis,
      h("div", { className: "cstd-grid" },
        attentionBlock,
        h("div", { className: "flex flex-col gap-4" }, systemCard, historyCard)
      )
    );
  }

  PLUGINS.register("custodian", CustodianDashboard);
})();
