variable "aws_region" {
  type    = string
  default = "eu-central-1"
}

variable "aws_profile" {
  description = "AWS CLI profile to use for authentication."
  type        = string
  default     = "evaluator-profile"
}

variable "environment" {
  description = "Environment name (used for tagging)."
  type        = string
  default     = "production"
}

variable "name_prefix" {
  type    = string
  default = "snaplogic-exercise-evaluator"
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
  default     = ["80.77.146.146"]
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

variable "budget_limit_usd" {
  description = "Monthly cost budget; email alert fires at 80% actual / 100% forecasted."
  type        = number
  default     = 10
}

variable "budget_alert_email" {
  type    = string
  default = "antonioapcevski@gmail.com"
}

variable "github_repo" {
  description = "owner/repo for the GitHub Actions OIDC trust."
  type        = string
  default     = "apchevski/snaplogic-exercise-evaluator"
}
