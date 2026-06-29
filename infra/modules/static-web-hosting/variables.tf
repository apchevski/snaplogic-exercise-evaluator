variable "name_prefix" {
  type = string
}

variable "spa_bucket_name" {
  description = "Globally unique bucket name for the built SPA."
  type        = string
}

variable "allowed_cidrs" {
  description = "CIDRs allowed through the CloudFront Function (VPN/office). Empty list = allow all (NOT recommended)."
  type        = list(string)
  default     = []
}

variable "tags" {
  type    = map(string)
  default = {}
}
