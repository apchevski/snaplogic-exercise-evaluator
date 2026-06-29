variable "name_prefix" {
  type = string
}

variable "domain_prefix" {
  description = "Globally unique Cognito hosted-UI domain prefix."
  type        = string
}

variable "callback_urls" {
  description = "Allowed OAuth callback URLs (the CloudFront URL once known; localhost for token testing in Phase 3)."
  type        = list(string)
  default     = ["http://localhost:5173/"]
}

variable "logout_urls" {
  type    = list(string)
  default = ["http://localhost:5173/"]
}

variable "tags" {
  type    = map(string)
  default = {}
}
