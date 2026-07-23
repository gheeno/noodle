output "mcp_url" {
  description = "The endpoint teams point their MCP client at (append /mcp)."
  value       = "https://${azurerm_container_app.noodle_mcp.latest_revision_fqdn}/mcp"
}

output "acr_login_server" {
  description = "Push images here: az acr build -t noodle-mcp:<tag> -r <this> ."
  value       = azurerm_container_registry.this.login_server
}

output "storage_account_name" {
  description = "Holds the noodle-workspaces file share — mount this to run `noodle init /data/<team>` when onboarding a team."
  value       = azurerm_storage_account.this.name
}

output "file_share_name" {
  value = azurerm_storage_share.workspaces.name
}

output "resource_group_name" {
  value = azurerm_resource_group.this.name
}

output "container_app_name" {
  value = azurerm_container_app.noodle_mcp.name
}
