/**
 * Custodian Dashboard Plugin — Grid-based dashboard UI.
 *
 * Layout: 3-column grid on desktop, stacking on mobile.
 *   Row 1: Status | Scan History | System
 *   Row 2: Escalations (left) | Auto-Resolved (right)
 *
 * States:
 *   clear   — all green, minimal UI
 *   attention — escalations shown prominently
 *
 * Uses the Hermes dashboard plugin SDK (no external deps).
 */

(function () {
  "use strict";

  var SDK = window.__HERMES_PLUGIN_SDK__;
  var PLUGINS = window.__HERMES_PLUGINS__;

  if (!SDK || !PLUGINS) {
    console.error("[custodian] Hermes plugin SDK not available.");
    return;
  }

  var React = SDK.React;
  var hooks = SDK.hooks;
  var fetchJSON = SDK.fetchJSON;
  var components = SDK.components;
  var utils = SDK.utils;

  var useState = hooks.useState;
  var useEffect = hooks.useEffect;
  var useCallback = hooks.useCallback;

  var Card = components.Card;
  var CardHeader = components.CardHeader;
  var CardTitle = components.CardTitle;
  var CardContent = components.CardContent;
  var Badge = components.Badge;
  var Button = components.Button;
  var Spinner = components.Spinner || function (props) {
    return React.createElement("span", { className: (props.className || "") + " animate-pulse" }, "…");
  };

  var cn = utils.cn;
  var timeAgo = utils.isoTimeAgo || function (s) { return s || ""; };

  // ---------------------------------------------------------------------------
  // Helpers
  // ---------------------------------------------------------------------------

  function tierBadgeTone(tier) {
    if (tier >= 4) return "destructive";
    if (tier === 3) return "warning";
    if (tier === 2) return "outline";
    return "success";
  }

  function tierLabel(tier) {
    var labels = { 1: "T1 Auto-fix", 2: "T2 Plan", 3: "T3 Escalate", 4: "T4 Critical" };
    return labels[tier] || ("T" + tier);
  }

  function healthIcon(state) {
    if (state === "clear") return "✓";
    return "⚠";
  }

  function healthColor(state) {
    if (state === "clear") return "text-green-500";
    return "text-yellow-500";
  }

  function formatTime(iso) {
    if (!iso) return "Never";
    try {
      var d = new Date(iso);
      return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    } catch (e) {
      return iso;
    }
  }

  function formatRelative(iso) {
    if (!iso) return "Never";
    try {
      var d = new Date(iso);
      var now = new Date();
      var diffMs = now - d;
      var diffMin = Math.floor(diffMs / 60000);
      if (diffMin < 1) return "just now";
      if (diffMin < 60) return diffMin + "m ago";
      var diffHr = Math.floor(diffMin / 60);
      if (diffHr < 24) return diffHr + "h ago";
      return Math.floor(diffHr / 24) + "d ago";
    } catch (e) {
      return iso;
    }
  }

  // ---------------------------------------------------------------------------
  // Status Card (top-left)
  // ---------------------------------------------------------------------------

  function StatusCard(props) {
    var health = props.health;
    var lastScan = props.lastScan;
    var issues = props.issues;

    return React.createElement(Card, null,
      React.createElement(CardHeader, { className: "pb-2" },
        React.createElement(CardTitle, { className: "text-sm" }, "Status")
      ),
      React.createElement(CardContent, null,
        React.createElement("div", { className: "flex flex-col items-center gap-3 py-2" },
          // Health icon
          React.createElement("span", {
            className: cn("text-3xl", healthColor(health.state))
          }, healthIcon(health.state)),

          // Label
          React.createElement("span", { className: "text-sm font-medium text-center" },
            health.label
          ),

          // Detail
          React.createElement("span", { className: "text-xs text-muted-foreground text-center" },
            health.detail
          ),

          // Stats row
          React.createElement("div", { className: "flex gap-3 text-xs text-muted-foreground" },
            issues.resolved_today > 0
              ? React.createElement("span", null, "✓ ", issues.resolved_today, " resolved today")
              : null,
            issues.open > 0
              ? React.createElement("span", null, issues.open, " open")
              : null
          ),

          // Last scan
          React.createElement("span", { className: "text-xs text-muted-foreground" },
            "Last scan: ", lastScan.age_minutes != null
              ? formatRelative(lastScan.at)
              : "Never"
          ),

          // Activity link
          React.createElement(Button, {
            variant: "ghost",
            size: "sm",
            className: "mt-1 text-xs",
            onClick: function () { if (props.onShowActivity) props.onShowActivity(); }
          }, "View Activity →")
        )
      )
    );
  }

  // ---------------------------------------------------------------------------
  // Scan History Card (top-center)
  // ---------------------------------------------------------------------------

  function ScanHistoryCard(props) {
    var scans = props.scans;
    var onScan = props.onScan;

    // Group by date
    var grouped = {};
    scans.forEach(function (s) {
      var date = (s.created_at || "").split("T")[0] || "Unknown";
      if (!grouped[date]) grouped[date] = [];
      grouped[date].push(s);
    });

    var dates = Object.keys(grouped).sort().reverse();

    return React.createElement(Card, null,
      React.createElement(CardHeader, { className: "pb-2" },
        React.createElement(CardTitle, { className: "text-sm" }, "Scan History")
      ),
      React.createElement(CardContent, null,
        React.createElement("div", { className: "flex flex-col gap-3" },
          dates.length === 0
            ? React.createElement("span", { className: "text-xs text-muted-foreground" }, "No scans recorded yet.")
            : dates.slice(0, 3).map(function (date) {
                var dayScans = grouped[date];
                return React.createElement("div", { key: date, className: "flex flex-col gap-1" },
                  React.createElement("span", { className: "text-xs font-medium text-muted-foreground" },
                    date === new Date().toISOString().split("T")[0] ? "Today" : date
                  ),
                  dayScans.slice(0, 4).map(function (scan, i) {
                    var hasIssues = scan.issues_found > 0;
                    var hasFixes = scan.fixes_applied > 0;
                    return React.createElement("div", {
                      key: i,
                      className: "flex items-center gap-2 text-xs"
                    },
                      React.createElement("span", { className: "text-muted-foreground w-12" },
                        formatTime(scan.created_at)
                      ),
                      React.createElement("span", {
                        className: cn(
                          "w-1.5 h-1.5 rounded-full",
                          hasFixes ? "bg-green-500" : hasIssues ? "bg-yellow-500" : "bg-muted-foreground"
                        )
                      }),
                      React.createElement("span", null,
                        hasIssues
                          ? scan.issues_found + " issue" + (scan.issues_found !== 1 ? "s" : "")
                          : "clean"
                      ),
                      hasFixes
                        ? React.createElement("span", { className: "text-green-500" },
                            "· " + scan.fixes_applied + " fixed")
                        : null,
                      scan.has_escalations
                        ? React.createElement("span", { className: "text-yellow-500" }, " · escalated")
                        : null
                    );
                  })
                );
              }),

          // Scan buttons
          React.createElement("div", { className: "flex gap-2 pt-1" },
            React.createElement(Button, {
              size: "sm",
              variant: "outline",
              onClick: function () { onScan("light"); }
            }, "▶ Light"),
            React.createElement(Button, {
              size: "sm",
              variant: "outline",
              onClick: function () { onScan("deep"); }
            }, "▶ Deep")
          )
        )
      )
    );
  }

  // ---------------------------------------------------------------------------
  // System Card (top-right)
  // ---------------------------------------------------------------------------

  function SystemCard(props) {
    var system = props.system;

    return React.createElement(Card, null,
      React.createElement(CardHeader, { className: "pb-2" },
        React.createElement(CardTitle, { className: "text-sm" }, "System")
      ),
      React.createElement(CardContent, null,
        React.createElement("div", { className: "flex flex-col gap-2 text-xs" },
          // Cron
          React.createElement("div", { className: "flex justify-between" },
            React.createElement("span", { className: "text-muted-foreground" }, "Cron"),
            React.createElement("span", null,
              system.cron.total, " jobs",
              system.cron.disabled > 0
                ? React.createElement("span", { className: "text-yellow-500 ml-1" },
                    "(", system.cron.disabled, " disabled)")
                : null
            )
          ),
          // Skills
          React.createElement("div", { className: "flex justify-between" },
            React.createElement("span", { className: "text-muted-foreground" }, "Skills"),
            React.createElement("span", null,
              system.skills.active, " active",
              system.skills.stale > 0
                ? React.createElement("span", { className: "text-yellow-500 ml-1" },
                    "(", system.skills.stale, " stale)")
                : null
            )
          ),
          // Gateway
          React.createElement("div", { className: "flex justify-between" },
            React.createElement("span", { className: "text-muted-foreground" }, "Gateway"),
            React.createElement("span", null, "uptime ", system.gateway.uptime)
          )
        )
      )
    );
  }

  // ---------------------------------------------------------------------------
  // Escalation Card (bottom-left, only shown when needed)
  // ---------------------------------------------------------------------------

  function EscalationCard(props) {
    var issues = props.issues;

    if (issues.length === 0) return null;

    return React.createElement("div", { className: "flex flex-col gap-3" },
      React.createElement("span", { className: "text-sm font-medium" },
        "Needs Attention (", issues.length, ")"
      ),
      issues.map(function (issue, i) {
        var tier = issue.tier || 3;
        var fpId = issue.fingerprint_id || issue.id || "unknown";
        var description = issue.description || issue.title || fpId;
        var autoFix = issue.auto_fix || "";

        return React.createElement(Card, {
          key: i,
          className: "border-l-2",
          style: { borderLeftColor: tier >= 4 ? "var(--color-destructive)" : "var(--color-warning)" }
        },
          React.createElement(CardContent, { className: "py-3" },
            React.createElement("div", { className: "flex flex-col gap-2" },
              // Header row
              React.createElement("div", { className: "flex items-center gap-2" },
                React.createElement(Badge, { tone: tierBadgeTone(tier) }, tierLabel(tier)),
                React.createElement("span", { className: "text-sm font-medium" }, description)
              ),

              // Detail
              React.createElement("span", { className: "text-xs text-muted-foreground" },
                issue.evidence || issue.detail || "Detected by Custodian scan."
              ),

              // What was tried
              autoFix
                ? React.createElement("span", { className: "text-xs text-muted-foreground italic" },
                    "Auto-fix: ", autoFix)
                : null,

              // Action
              React.createElement("div", { className: "pt-1" },
                React.createElement(Button, {
                  size: "sm",
                  variant: tier >= 4 ? "destructive" : "outline",
                  onClick: function () {
                    // Open relevant page or trigger action
                    if (fpId.indexOf("oauth") !== -1 || fpId.indexOf("google") !== -1) {
                      window.open("https://console.cloud.google.com/apis/credentials", "_blank");
                    } else if (fpId.indexOf("disk") !== -1) {
                      // Could trigger a disk usage scan
                      console.log("[custodian] Disk issue — manual review needed");
                    }
                  }
                }, tier >= 4 ? "Resolve" : "Review →")
              )
            )
          )
        );
      })
    );
  }

  // ---------------------------------------------------------------------------
  // Auto-Resolved Grid (bottom-right)
  // ---------------------------------------------------------------------------

  function AutoResolvedGrid(props) {
    var fixes = props.fixes;

    if (fixes.length === 0) return null;

    return React.createElement("div", { className: "flex flex-col gap-3" },
      React.createElement("span", { className: "text-sm font-medium" },
        "Auto-Resolved (", fixes.length, ")"
      ),
      React.createElement("div", { className: "grid grid-cols-2 sm:grid-cols-3 gap-2" },
        fixes.map(function (fix, i) {
          return React.createElement(Card, { key: i, className: "border-border/50" },
            React.createElement(CardContent, { className: "py-2 px-3" },
              React.createElement("div", { className: "flex items-start gap-2" },
                React.createElement("span", { className: "text-green-500 text-xs mt-0.5" }, "✓"),
                React.createElement("div", { className: "flex flex-col gap-0.5 min-w-0" },
                  React.createElement("span", { className: "text-xs font-medium truncate" },
                    fix.fingerprint || fix.fingerprint_id || "Unknown"
                  ),
                  React.createElement("span", { className: "text-xs text-muted-foreground truncate" },
                    fix.description || fix.command || "Fixed"
                  )
                )
              )
            )
          );
        })
      )
    );
  }

  // ---------------------------------------------------------------------------
  // Main Dashboard
  // ---------------------------------------------------------------------------

  function CustodianDashboard() {
    var dataState = useState(null);
    var data = dataState[0];
    var setData = dataState[1];
    var loadingState = useState(true);
    var loading = loadingState[0];
    var setLoading = loadingState[1];
    var errorState = useState(null);
    var error = errorState[0];
    var setError = errorState[1];
    var scanningState = useState(false);
    var scanning = scanningState[0];
    var setScanning = scanningState[1];

    var fetchData = useCallback(function () {
      setLoading(true);
      setError(null);
      fetchJSON("/api/plugins/custodian/status")
        .then(function (result) {
          setData(result);
          setLoading(false);
        })
        .catch(function (err) {
          setError(err.message || "Failed to load status");
          setLoading(false);
        });
    }, []);

    useEffect(function () {
      fetchData();
      // Auto-refresh every 60s
      var interval = setInterval(fetchData, 60000);
      return function () { clearInterval(interval); };
    }, [fetchData]);

    var handleScan = useCallback(function (mode) {
      setScanning(true);
      fetchJSON("/api/plugins/custodian/scan?mode=" + mode, { method: "POST" })
        .then(function () {
          // Refresh data after scan
          setTimeout(fetchData, 1000);
        })
        .catch(function (err) {
          setError("Scan failed: " + (err.message || "unknown error"));
        })
        .finally(function () {
          setScanning(false);
        });
    }, [fetchData]);

    // Loading state
    if (loading && !data) {
      return React.createElement(
        "div",
        { className: "flex items-center justify-center gap-2 p-8 text-sm text-muted-foreground" },
        React.createElement(Spinner, { className: "h-4 w-4" }),
        "Loading Custodian status…"
      );
    }

    // Error state
    if (error && !data) {
      return React.createElement(
        "div",
        { className: "p-4 text-sm text-destructive", role: "alert" },
        "Error: ", error,
        React.createElement(Button, { size: "sm", variant: "outline", onClick: fetchData, className: "ml-2" }, "Retry")
      );
    }

    if (!data) return null;

    var health = data.health || {};
    var issues = data.issues || {};
    var system = data.system || {};
    var scans = data.scan_history || [];
    var escalations = data.escalations || [];

    // Build auto-resolved list from scan history
    var autoResolved = [];
    scans.forEach(function (s) {
      if (s.fixes_applied > 0) {
        autoResolved.push({
          fingerprint: s.run_id,
          description: s.fixes_applied + " fix" + (s.fixes_applied !== 1 ? "es" : "") + " · " + formatRelative(s.created_at),
        });
      }
    });

    var hasEscalations = escalations.length > 0;

    return React.createElement(
      "div",
      { className: "custodian-dashboard p-4" },
      // Row 1: Three-column grid
      React.createElement("div", { className: "custodian-grid-row1" },
        React.createElement(StatusCard, {
          health: health,
          lastScan: data.last_scan || {},
          issues: issues,
        }),
        React.createElement(ScanHistoryCard, {
          scans: scans,
          onScan: handleScan,
        }),
        React.createElement(SystemCard, {
          system: system,
        })
      ),

      // Row 2: Escalations + Auto-Resolved
      hasEscalations
        ? React.createElement("div", { className: "custodian-grid-row2" },
            React.createElement(EscalationCard, { issues: escalations }),
            autoResolved.length > 0
              ? React.createElement(AutoResolvedGrid, { fixes: autoResolved })
              : null
          )
        : autoResolved.length > 0
          ? React.createElement("div", { className: "custodian-grid-row2-single" },
              React.createElement(AutoResolvedGrid, { fixes: autoResolved })
            )
          : null,

      // Scanning overlay
      scanning
        ? React.createElement("div", { className: "custodian-scanning" },
            React.createElement("div", { className: "flex items-center gap-2 text-sm" },
              React.createElement(Spinner, { className: "h-4 w-4" }),
              "Running scan…"
            )
          )
        : null
    );
  }

  // ---------------------------------------------------------------------------
  // Register
  // ---------------------------------------------------------------------------

  PLUGINS.register("custodian", CustodianDashboard);
})();
