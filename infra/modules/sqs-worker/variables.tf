variable "name_prefix" {
  type = string
}

variable "image_uri" {
  description = "Full ECR image URI incl. tag, e.g. <acct>.dkr.ecr.<region>.amazonaws.com/<repo>:latest"
  type        = string
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

variable "secret_arn" {
  type = string
}

variable "judge_model" {
  type    = string
  default = "claude-sonnet-5" # locked decision (2026-07-12, was sonnet-4-6) — do not silently upgrade
}

variable "log_retention_days" {
  type    = number
  default = 7
}

variable "tags" {
  type    = map(string)
  default = {}
}
