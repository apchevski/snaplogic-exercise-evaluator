provider "aws" {
  region = var.aws_region
  default_tags {
    tags = local.tags
  }
  profile = var.aws_profile
}