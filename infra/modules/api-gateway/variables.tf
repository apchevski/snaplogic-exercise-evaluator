variable "name_prefix" {
  type = string
}

variable "image_uri" {
  type = string
}

variable "table_name" {
  type = string
}

variable "table_arn" {
  type = string
}

variable "bucket_name" {
  type = string
}

variable "bucket_arn" {
  type = string
}

variable "queue_url" {
  type = string
}

variable "queue_arn" {
  type = string
}

variable "secret_arn" {
  description = "App secret with the SnapLogic credentials — POST /v1/students verifies the student's project exists before registering."
  type        = string
}

variable "user_pool_id" {
  description = "Cognito user pool the API creates student logins in (POST /v1/students with an email)."
  type        = string
}

variable "user_pool_arn" {
  type = string
}

variable "jwt_issuer" {
  type = string
}

variable "jwt_audience" {
  description = "Cognito app client id."
  type        = string
}

variable "cors_allow_origins" {
  description = "Browser origins allowed to call the API (the CloudFront URL; localhost for dev)."
  type        = list(string)
  default     = ["http://localhost:5173"]
}

variable "allowed_cidrs" {
  description = "Comma-joined into ALLOWED_CIDRS for the Lambda's source-ip re-check. Empty list disables the inner check."
  type        = list(string)
  default     = []
}

variable "log_retention_days" {
  type    = number
  default = 7
}

variable "tags" {
  type    = map(string)
  default = {}
}
