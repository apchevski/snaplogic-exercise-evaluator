variable "name_prefix" {
  description = "Prefix for resource names, e.g. 'snaplogic-evaluator'."
  type        = string
}

variable "data_bucket_name" {
  description = "Globally unique name for the reports/artifacts bucket."
  type        = string
}

variable "cors_allow_origins" {
  description = <<-EOT
    Browser origins allowed to PUT directly to the bucket (presigned uploads
    from the SPA's Add New Exercise dialog). Empty list = no CORS config.
  EOT
  type        = list(string)
  default     = []
}

variable "tags" {
  type    = map(string)
  default = {}
}
