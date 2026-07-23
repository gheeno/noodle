resource "azurerm_resource_group" "this" {
  name     = var.resource_group_name
  location = var.location
}

# ACR and storage account names are globally unique across Azure — a plain
# prefix-based name collides the moment anyone else deploys this stack.
resource "random_string" "suffix" {
  length  = 6
  special = false
  upper   = false
}

# ---------------------------------------------------------------------------
# Registry — holds the image built from the repo root Dockerfile
# (`az acr build -t noodle-mcp:<tag> -r <acr_name> .`). No Dockerfile change
# needed: the same image that runs `noodle run` in CI runs `noodle-mcp` here,
# just with the container command/args overridden below.
# ---------------------------------------------------------------------------
resource "azurerm_container_registry" "this" {
  name                = "${replace("${var.prefix}acr", "-", "")}${random_string.suffix.result}"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  sku                 = "Basic"
  admin_enabled       = false
}

resource "azurerm_user_assigned_identity" "acr_pull" {
  name                = "${var.prefix}-acrpull"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
}

resource "azurerm_role_assignment" "acr_pull" {
  scope                = azurerm_container_registry.this.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.acr_pull.principal_id
}

# ---------------------------------------------------------------------------
# Persistent workspace storage — /data on the container, one subfolder per
# team (e.g. /data/team-b), each scaffolded once with `noodle init /data/team-b`
# (see README). Without this, every redeploy/restart wipes every team's
# tests, POMs, and artifacts — Container Apps' local disk is ephemeral.
# ---------------------------------------------------------------------------
resource "azurerm_storage_account" "this" {
  name                     = substr("${replace("${var.prefix}stg", "-", "")}${random_string.suffix.result}", 0, 24)
  resource_group_name     = azurerm_resource_group.this.name
  location                 = azurerm_resource_group.this.location
  account_tier             = "Standard"
  account_replication_type = "LRS"
}

resource "azurerm_storage_share" "workspaces" {
  name                 = "noodle-workspaces"
  storage_account_name = azurerm_storage_account.this.name
  quota                = var.file_share_quota_gb
}

resource "azurerm_log_analytics_workspace" "this" {
  name                = "${var.prefix}-logs"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  sku                 = "PerGB2018"
  retention_in_days   = 30
}

resource "azurerm_container_app_environment" "this" {
  name                       = "${var.prefix}-env"
  resource_group_name        = azurerm_resource_group.this.name
  location                   = azurerm_resource_group.this.location
  log_analytics_workspace_id = azurerm_log_analytics_workspace.this.id
}

resource "azurerm_container_app_environment_storage" "workspaces" {
  name                         = "workspace-data"
  container_app_environment_id = azurerm_container_app_environment.this.id
  account_name                  = azurerm_storage_account.this.name
  share_name                    = azurerm_storage_share.workspaces.name
  access_key                    = azurerm_storage_account.this.primary_access_key
  access_mode                   = "ReadWrite"
}

# ---------------------------------------------------------------------------
# The server itself. min_replicas = max_replicas = 1 deliberately: noodle-mcp
# keeps per-workspace run state (artifacts/agent_state.json) on disk with no
# cross-replica locking, so scaling out would let two replicas race on the
# same team's "last test" file. Scale CPU/memory (variables.tf) before
# replica count; if concurrent-team throughput becomes the bottleneck, shard
# by team across multiple Container Apps instead of scaling this one out.
# ---------------------------------------------------------------------------
resource "azurerm_container_app" "noodle_mcp" {
  name                         = var.prefix
  container_app_environment_id = azurerm_container_app_environment.this.id
  resource_group_name          = azurerm_resource_group.this.name
  revision_mode                = "Single"

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.acr_pull.id]
  }

  registry {
    server   = azurerm_container_registry.this.login_server
    identity = azurerm_user_assigned_identity.acr_pull.id
  }

  secret {
    name  = "noodle-mcp-api-key"
    value = var.noodle_mcp_api_key
  }

  template {
    min_replicas = 1
    max_replicas = 1

    volume {
      name         = "workspace-data"
      storage_type = "AzureFile"
      storage_name = azurerm_container_app_environment_storage.workspaces.name
    }

    container {
      name   = "noodle-mcp"
      image  = "${azurerm_container_registry.this.login_server}/noodle-mcp:${var.image_tag}"
      cpu    = var.container_cpu
      memory = var.container_memory

      # Overrides the image's default ENTRYPOINT/CMD (`noodle run features/`)
      # — same image, server mode instead of one-shot CI run.
      command = ["noodle-mcp"]
      args = [
        "--workspace", "/data/_base",
        "--workspace-root", "/data",
        "--transport", "streamable-http",
        "--host", "0.0.0.0",
        "--port", "8080",
      ]

      env {
        name        = "NOODLE_MCP_API_KEY"
        secret_name = "noodle-mcp-api-key"
      }
      env {
        name  = "NOODLE_HEADLESS"
        value = "true"
      }

      volume_mounts {
        name = "workspace-data"
        path = "/data"
      }

      liveness_probe {
        transport = "TCP"
        port      = 8080
      }
    }
  }

  ingress {
    external_enabled = true
    target_port       = 8080
    transport          = "http"

    traffic_weight {
      latest_revision = true
      percentage       = 100
    }
  }
}
