variable "aws_region" {
  type    = string
  default = "eu-central-1"
}

variable "name_prefix" {
  type    = string
  default = "snaplogic-evaluator"
}

variable "data_bucket_name" {
  description = "Globally unique bucket for reports + exercise artifacts."
  type        = string
}

variable "spa_bucket_name" {
  description = "Globally unique bucket for the built SPA."
  type        = string
}

variable "cognito_domain_prefix" {
  description = "Globally unique Cognito hosted-UI domain prefix."
  type        = string
}

variable "image_tag" {
  description = "Image tag both Lambdas run. CI repoints code via update-function-code; bump here only for pinning."
  type        = string
  default     = "latest"
}

variable "judge_model" {
  type    = string
  default = "claude-sonnet-4-6"
}

variable "allowed_cidrs" {
  description = "Office/VPN CIDRs allowed through CloudFront + the API Lambda."
  type        = list(string)
  default     = []
}

variable "extra_callback_urls" {
  description = "Additional Cognito callback/logout URLs (keep localhost while developing the SPA)."
  type        = list(string)
  default     = ["http://localhost:5173/"]
}

variable "extra_cors_origins" {
  description = "Additional CORS origins for the API (localhost for dev)."
  type        = list(string)
  default     = ["http://localhost:5173"]
}

variable "budget_alert_email" {
  type = string
}

variable "github_repo" {
  description = "owner/repo for the GitHub Actions OIDC trust."
  type        = string
}
