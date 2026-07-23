# noodle-mcp on Azure Container Apps

Stands up a single, always-on `noodle-mcp` server (streamable-http
transport) that multiple teams share, each isolated to their own workspace
folder on a mounted Azure Files share. No team clones this repo — they add
the server as a remote MCP tool and talk to it in plain English. Background:
[`docs/mcp-guide.md` §8](../../../docs/mcp-guide.md#8-maf--azure-ai-foundry-integration-remote-streamable-http)
and [`docs/ai-sdlc-integration.md` §4/§6](../../../docs/ai-sdlc-integration.md#4-hooking-up-an-ai-sdlc-agent).

## What this deploys

- Azure Container Registry (image pulled via a user-assigned managed
  identity — no admin credentials, no pull secret to rotate)
- Container Apps Environment + Log Analytics
- Storage account + Azure Files share, mounted at `/data` in the container
  — this is what makes teams' tests, POMs, and Allure/RCA artifacts survive
  restarts and redeploys
- One Container App running the **same image `Dockerfile` already builds**,
  with its command overridden from `noodle run features/` (CI mode) to
  `noodle-mcp --transport streamable-http` (server mode) — no Dockerfile
  change needed
- `min_replicas = max_replicas = 1`, on purpose: `noodle-mcp` keeps
  per-workspace run state (`artifacts/agent_state.json`, "the last test")
  on disk with no cross-replica locking. Scale CPU/memory
  (`container_cpu`/`container_memory`) before you'd ever scale replica
  count; if concurrent-team load becomes the bottleneck, shard by team
  across separate Container Apps rather than scaling this one out.

## Deploy

Prerequisites: `az login` (or service principal env vars the `azurerm`
provider recognizes — `ARM_CLIENT_ID`/`ARM_CLIENT_SECRET`/`ARM_TENANT_ID`/
`ARM_SUBSCRIPTION_ID`) and `terraform >= 1.5`.

The registry doesn't exist until Terraform creates it, so the first apply
is two steps: create the ACR, push an image into it, then apply the rest.

```bash
cd infra/terraform/azure-container-apps
terraform init

# 1. Create just the registry (ACR name is auto-suffixed for global
#    uniqueness, so it isn't known until this runs)
terraform apply -target=azurerm_container_registry.this \
  -var="image_tag=placeholder" \
  -var="noodle_mcp_api_key=$(openssl rand -hex 24)"

# 2. Build & push the image (from the repo root, same Dockerfile CI uses)
ACR_NAME=$(terraform output -raw acr_login_server | cut -d. -f1)
az acr build -t noodle-mcp:$(git rev-parse --short HEAD) -r "$ACR_NAME" ../../..

# 3. Apply everything else, pointing at the real tag
terraform apply \
  -var="image_tag=$(git rev-parse --short HEAD)" \
  -var="noodle_mcp_api_key=$(openssl rand -hex 24)"   # same key as step 1
```

Keep the `noodle_mcp_api_key` value identical across applies (export it to
a shell variable, or put it in a `terraform.tfvars` you don't commit) —
passing a different value on step 3 rotates the key Terraform thinks is
current without you noticing.

**Save the API key** you passed in (`terraform apply` doesn't print
secrets back) — put it in Key Vault, hand it to teams out of band, never in
a prompt or committed config.

## Onboarding a new team

Each team needs its own scaffolded workspace under `/data` before their
first MCP call — the Container App mounts the share, but nothing runs
`noodle init` for you.

```bash
az containerapp exec \
  --name $(terraform output -raw container_app_name) \
  --resource-group $(terraform output -raw resource_group_name) \
  --command "noodle init /data/<team-name>"
```

Repeat per team (`team-b`, `team-checkout`, …) — each gets its own
`noodle.yaml`/`tests/`/`.env` under `/data/<team-name>`, isolated from every
other team's tests, artifacts, and `agent_state.json` (env var collisions
are per-process, not per-workspace — see `docs/agent-playbook.md`'s
collision rule, so this also keeps two teams' `{env:...}` keys from
colliding if either forgets to prefix them).

## Connecting a team's Claude CLI

```bash
claude mcp add --transport http noodle "$(terraform output -raw mcp_url)" \
  --header "Authorization: Bearer <the API key>"
```

Then, since one server serves every team, tell the agent which workspace to
use in the prompt (there's no per-team key — the `workspace` argument on
each tool call is what scopes a request, see `mcp-guide.md` §4):

> "Using the noodle MCP tools with workspace /data/team-b, generate a test
> for checkout on staging.example.com, run it, and show me the report."

Claude fills `workspace="/data/team-b"` on every `generate_test`/`run_test`/
`get_rca` call from there. `--workspace-root /data` on the server (set in
`main.tf`) is what allows that override at all — without it, remote callers
would be locked to `/data/_base` for every call (see `mcp-guide.md` §10,
gotcha 2).

## Operational notes

- **No hot reload.** After pushing a new image tag, `terraform apply
  -var="image_tag=<new tag>"` creates a new revision; the old process
  doesn't pick up code changes on its own. Confirm with the `server_info`
  MCP tool (`started_at`/`pid`) after a deploy.
- **Rotating the API key:** `terraform apply -var="noodle_mcp_api_key=<new
  key>"`, then every team re-runs their `claude mcp add` (or edits
  `.claude.json`) with the new value. There's one shared key for the whole
  server (`docs/mcp-guide.md` §9) — if teams need independently
  revocable credentials, put API Management or another gateway in front and
  issue per-team keys there; Noodle itself only checks one shared secret.
- **Backups:** the Azure Files share is the only copy of every team's
  in-flight `.feature` files until they're pushed to a real tests repo
  (`docs/ai-sdlc-integration.md` §5 step 4) — turn on share soft-delete /
  snapshots if teams will treat this as more than scratch space.
