from __future__ import annotations

LOGGER_NAME = "arc.mcp_polygon"

SBT_TOOL_ROLES = {
    "hasSbt": "Read-only",
    "getScore": "Read-only",
    "issueScore": "Owner",
    "revokeScore": "Owner",
}

POOL_TOOL_ROLES = {
    "availableLiquidity": "Read-only",
    "lenderBalance": "Read-only",
    "getLoan": "Read-only",
    "isBanned": "Read-only",
    "deposit": "Lender",
    "withdraw": "Lender",
    "openLoan": "Owner",
    "repay": "Borrower",
    "checkDefaultAndBan": "Owner",
    "unban": "Owner",
}

MCP_BRIDGE_SESSION_KEY = "mcp_cctp_bridge_state"
MCP_ARC_TRANSFER_SESSION_KEY = "mcp_arc_transfer_state"

MCP_POLYGON_COMMAND_KEY = "mcp_polygon_wallet_command"
MCP_POLYGON_COMMAND_SEQ_KEY = "mcp_polygon_wallet_command_seq"
MCP_POLYGON_COMMAND_ARGS_KEY = "mcp_polygon_wallet_command_args"
MCP_POLYGON_COMMAND_REASON_KEY = "mcp_polygon_wallet_command_reason"
MCP_POLYGON_COMMAND_LOGGED_KEY = "mcp_polygon_wallet_command_logged"
MCP_POLYGON_LOGS_KEY = "mcp_polygon_wallet_logs"
MCP_POLYGON_PENDING_TX_KEY = "mcp_polygon_pending_tx_request"
MCP_BORROWER_BRIDGE_SESSION_KEY = "mcp_borrower_bridge_session"
MCP_POLYGON_WALLET_STATE_KEY = "mcp_polygon_wallet_state"
MCP_POLYGON_AUTO_SWITCH_KEY = "mcp_polygon_auto_switch_attempted"
MCP_POLYGON_STATUS_KEY = "mcp_polygon_status_message"
MCP_POLYGON_COMPLETE_KEY = "mcp_polygon_completion_state"

ATTESTATION_POLL_INTERVAL = 5
ATTESTATION_TIMEOUT = 600
ATTESTATION_INITIAL_TIMEOUT = 30
