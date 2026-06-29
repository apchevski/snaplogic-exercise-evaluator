variable "name_prefix" {
  description = "Prefix for resource names, e.g. 'snaplogic-evaluator'."
  type        = string
}

variable "data_bucket_name" {
  description = "Globally unique name for the reports/artifacts bucket."
  type        = string
}

variable "tags" {
  type    = map(string)
  default = {}
}
