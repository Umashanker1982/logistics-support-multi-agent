# Agents

Each agent has one job, typed inputs and outputs (`agents/types.py`), and a fixed set of tools enforced by `security/permissions.py`.

| Agent | Input | Output | Tools allowed | Model | Prompt |
|---|---|---|---|---|---|
| Orchestrator | message, customer_id, session_id | `AgentResponse` | none directly | – | – |
| Router | `IntentResult` | `RouteDecision` | none | – | – |
| Intent agent | message, `SessionMemory` | `IntentResult` | **none** | Claude Haiku, rules fallback | `INTENT_SYSTEM` |
| Lookup agent | intent, order_id, customer_id | `Facts` | read-only: `get_order`, `get_shipment`, `get_return_status`, `get_refund_status`, `get_claim_status` | Claude tool-use, verified by code | `LOOKUP_SYSTEM` |
| Policy agent | intent, order_id, `Facts` | `PolicyDecision` | `check_refund_eligibility`, `check_claim_eligibility` | **none, by design** | – |
| Execution agent | `PolicyDecision` | `ExecutionResult` | **write**: `issue_refund`, `file_claim`, `create_ticket` | **none, by design** | – |
| Response agent | fact sheet | reply, citations | **none** | Claude Haiku, templates fallback | `RESPONSE_SYSTEM` |
| General agent | message | reply | **none** | Claude Haiku, FAQ fallback | `GENERAL_SYSTEM` |

Prompts are in `agents/prompts.py`. Each one:
- states a single job first;
- wraps customer text in `<customer_message>` tags and declares it untrusted;
- fixes the output format so code can validate it;
- lists what the model must not do.

## Why the policy and execution agents have no model

These two agents decide and move money.
- Every decision must be **reproducible**: the same facts always give the same answer.
- Every decision must be **explainable**: each one cites rule ids R-1..R-5, I-1..I-5, or T-1.
- Every decision must be **testable**: `tests/test_tools.py` covers each rule branch.

A model in this position would add a way to talk the system into a payout without adding value. The models handle what they are good at: understanding free text, choosing lookups, and writing clear replies.

## How model output is constrained

| Agent | Constraint |
|---|---|
| Intent | Output must parse as JSON with an allowed intent. An order id the model proposes is discarded unless it literally appears in the message. |
| Lookup | Every tool call is pinned to the one resolved order id. Other ids get `OUT_OF_SCOPE`. Afterwards, code checks that the required facts were gathered and fills any gap. |
| Response | The draft must cite only allowed sources, mention only the customer's own orders, and not claim money moved unless it did. Otherwise the template is used. |
| All | Any API error, timeout, or invalid output falls back to deterministic behaviour and is recorded as a trace event. |
