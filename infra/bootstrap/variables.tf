variable "aws_region" {
  description = "Region for the state bucket (match environments/production)."
  type        = string
  default     = "eu-central-1"
}

variable "aws_profile" {
  description = "AWS CLI profile to use for authentication."
  type        = string
  default     = "evaluator-profile"
}

variable "state_bucket_name" {
  description = "Globally unique name for the Terraform state bucket."
  type        = string
  default     = "snaplogic-exercise-evaluator-states"
}
