"""Tool schemas for Custodian plugin tools."""

CUSTODIAN_STATUS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "custodian_status",
        "description": "Show Custodian plugin status: enabled/disabled, last scan time, open issues count, confidence model summary.",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
}

CUSTODIAN_SCAN_SCHEMA = {
    "type": "function",
    "function": {
        "name": "custodian_scan",
        "description": "Run a Custodian scan. Use 'light' for quick check (tail log, cron registry, failed fixes). Use 'deep' for full 13-step sweep (activity model, schedule optimization, skill conformance, repair pass).",
        "parameters": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["light", "deep"],
                    "description": "Scan mode: 'light' for quick heartbeat check, 'deep' for full sweep.",
                },
            },
            "required": ["mode"],
        },
    },
}

CUSTODIAN_ISSUES_SCHEMA = {
    "type": "function",
    "function": {
        "name": "custodian_issues",
        "description": "List, filter, or resolve Custodian issues. Actions: 'list' (show open issues), 'resolve' (mark resolved by ID), 'summary' (counts by tier).",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "resolve", "summary"],
                    "description": "Issue action",
                },
                "issue_id": {
                    "type": "string",
                    "description": "Issue ID (required for 'resolve')",
                },
            },
            "required": ["action"],
        },
    },
}

ALL_SCHEMAS = [
    CUSTODIAN_STATUS_SCHEMA,
    CUSTODIAN_SCAN_SCHEMA,
    CUSTODIAN_ISSUES_SCHEMA,
]
