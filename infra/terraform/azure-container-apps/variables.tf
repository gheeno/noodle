variable "location" {
  description = "Azure region."
  type        = string
  default     = "eastus"
}

variable "resource_group_name" {
  description = "Resource group to create (or reuse) for the noodle-mcp deployment."
  type        = string
  default     = "rg-noodle-mcp"
}

variable "prefix" {
  description = "Name prefix applied to every resource this stack creates."
  type        = string
  default     = "noodle-mcp"
}

variable "image_tag" {
  description = <<-EOT
    Tag of the noodle-mcp image in ACR to deploy, e.g. a short git SHA.
    Built with: az acr build -t noodle-mcp:<tag> -r <acr_name> . (from the repo root Dockerfile)
  EOT
  type        = string
}

variable "noodle_mcp_api_key" {
  description = "Bearer token every team's MCP client must send. Generate with `openssl rand -hex 24`. One shared key for the whole server (see mcp-guide.md §9) — front with APIM/a gateway later if per-team keys are needed."
  type        = string
  sensitive   = true
}

variable "container_cpu" {
  description = "vCPU allocated to the noodle-mcp container. Playwright is memory/CPU heavy under concurrent runs; bump before adding teams, not after it falls over."
  type        = number
  default     = 2.0
}

variable "container_memory" {
  description = "Memory allocated to the noodle-mcp container. Must be a valid Container Apps cpu/memory combination for the value above."
  type        = string
  default     = "4Gi"
}

variable "file_share_quota_gb" {
  description = "Size of the Azure Files share backing /data (all teams' workspaces, tests, and artifacts)."
  type        = number
  default     = 100
}
