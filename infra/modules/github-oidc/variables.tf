variable "name_prefix" {
  type = string
}

variable "github_repo" {
  description = "owner/repo allowed to assume the deploy role."
  type        = string
}

variable "tags" {
  type    = map(string)
  default = {}
}
