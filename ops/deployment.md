# Deployment Guide

## 1. Local / single host (Docker Compose, minimum)

```bash
cp .env.example .env               # add ANTHROPIC_API_KEY to enable Claude mode
docker compose up --build          # web demo on http://localhost:8000
docker compose run --rm cli demo   # CLI walkthrough
docker compose run --rm cli approvals list
docker compose --profile eval run --rm benchmark     # regression gate inside the image
```

## 1b. Hosted demo (Render, as used for the live demo)

1. Push the repository to GitHub.
2. In Render: **New → Blueprint**, select the repository. `render.yaml` defines one Docker web service with a `/healthz` health check.
3. When prompted, paste `ANTHROPIC_API_KEY`. It's stored as a Render secret, never in git.
4. Render builds the same Dockerfile and injects `$PORT`. Every push to `main` redeploys automatically.
5. Plan: `free` sleeps after 15 idle minutes (about a minute to wake). Switch to `starter` for an always-on demo day.

Resilience built into the web service, so the demo stays up:
- Claude failures fall back to rules per request.
- A circuit breaker skips Claude for 5 minutes after repeated failures.
- `LLM_TIMEOUT_S=12` and `LLM_MAX_RETRIES=1` keep the live site responsive.
- A crash inside one request resets only that visitor's sandbox.
- A global error handler keeps the server running.
- The stream times out after 90 s instead of hanging.
- Render restarts the container if `/healthz` fails.
- Run a **single worker**: sandboxes live in process memory, so multiple workers would split a visitor's state. Scale up the instance size, not the worker count. Production would move state to a shared store.

**Maintenance switch:** if something goes badly wrong, set `MAINTENANCE_MODE=1` in the Render dashboard (Environment). Every visitor then sees a branded "Back shortly" page (HTTP 503), while `/healthz` stays green so Render doesn't restart-loop. Remove the variable to go live again.

Public-demo protections (all configurable by environment variable):
- an isolated sandbox per visitor;
- `DEMO_MSGS_PER_10MIN` (30 per visitor) and `DEMO_IP_MSGS_PER_10MIN` (80 per IP);
- `DEMO_DAILY_CLAUDE_REQUESTS` (300/day), after which replies fall back to rules-only mode;
- the API key's own spend limit in the Anthropic Console as the final backstop.

Container hardening already in `Dockerfile` and `docker-compose.yml`:
- non-root user (uid 10001);
- read-only root filesystem with writes only to the `/data` volume and `/tmp`;
- `no-new-privileges`;
- CPU and memory limits (0.5 CPU, 256 MB).

State (sessions, approvals, ledger, traces, audit) lives in the named volume `support-data`.

## 2. Production target (AWS example; Azure and GCP are equivalent)

```
Customer web/app ──HTTPS──> API Gateway / ALB (WAF, rate limit, JWT auth)
                               │  customer_id from the verified token, never from the message
                               ▼
                    ECS Fargate service: support-api  (2+ tasks, multi-AZ, private subnets)
                               │
     ┌──────────────┬──────────┼───────────────┬──────────────────────┐
     ▼              ▼          ▼               ▼                      ▼
 Orders/Refunds  DynamoDB    SQS queue     CloudWatch / OTel      Claude API via VPC egress
 internal APIs   sessions,   approvals ->   traces, metrics,       (or Amazon Bedrock if the
 (replace tools/ approvals,  Support-lead   alerts (see            customer requires in-VPC
 backend.py)     idempotency console (SSO)  observability.md)      inference + CloudTrail)
```

| Concern | Choice | Why |
|---|---|---|
| Compute | Containers on ECS Fargate (or Kubernetes) | Stateless app with no servers to patch; same image as local |
| Session and approval state | DynamoDB with TTL on sessions | Replaces JSON files; conditional writes give the same idempotency guarantees as `ApprovalQueue` and `issue_refund` |
| Approvals UI | Small internal web app behind SSO + MFA | Replaces the CLI; the approver identity comes from SSO |
| Model access | Claude API over egress, or Claude on Amazon Bedrock | Bedrock if the customer needs IAM, CloudTrail, and VPC endpoints for inference |
| Backends | Real order/refund/claims APIs | `tools/*.py` keep the same signatures; only `Backend` changes |

## 3. Secrets and configuration

- **All configuration comes from environment variables** (`config.py` is the only reader). `.env.example` documents every variable, and `.env` is git-ignored.
- **Secrets** (`ANTHROPIC_API_KEY`, backend API credentials) are stored in **AWS Secrets Manager** (or Azure Key Vault / GCP Secret Manager). They're injected as environment variables by the task definition and never baked into images or logs. Rotate every 90 days or immediately on suspicion.
- **IAM:**
  - The task role can read only its own secrets, read and write only its own DynamoDB tables, and publish to its own queue.
  - Separate roles for the approvals console and for CI.
- Non-secret settings (`REFUND_APPROVAL_THRESHOLD`, `CLAUDE_MODEL`, prices) are in SSM Parameter Store, so changes are audited.

## 4. Scaling

- The app is stateless per request (state is external), so it scales horizontally.
- Target tracking on CPU at 60% and on request count. Minimum 2 tasks for availability.
- **Model rate limits** are the real ceiling. Handle them with:
  - SDK retries with backoff (`LLM_MAX_RETRIES`);
  - a concurrency cap per task;
  - automatic fallback to the deterministic path when throttled. The fallback is correct but plainer, so throttling never causes failed requests.
- Cost scales linearly: at roughly $0.001 per request, 100k requests per month costs about $100 in model spend.

## 5. Release and rollback

1. **CI** (every push): tests, offline benchmark ≥ 95%, Docker build.
2. **Pre-release:** Claude-mode benchmark on golden and held-out sets, compared with the previous release's `eval/results/`.
3. **Deploy:** new task definition revision with a **canary at 10%** for 30 minutes, watching:
   - error rate,
   - fallback rate,
   - output-guardrail rejections,
   - outcome mix.
4. **Promote** to 100% if the canary is within thresholds.
5. **Rollback:**
   - Re-point the service to the previous task definition revision (one command, about 2 minutes). Images are immutable and tagged by git SHA.
   - Prompt or model changes are config: revert `CLAUDE_MODEL` or redeploy the previous image.
   - **Kill switch:** set `LLM_MODE=offline` to run fully deterministic with no model dependency, in minutes and without a code change.
   - Data: tool writes are idempotent and recorded in the audit log, so a rollback never double-pays. Refunds issued by a bad release can be listed by `trace_id` from the audit log.
