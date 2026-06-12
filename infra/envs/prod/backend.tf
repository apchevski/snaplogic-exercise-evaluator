# Remote state in the bucket created by infra/bootstrap. `bucket` cannot be a
# variable — fill it in (or pass -backend-config) after running bootstrap:
#
#   terraform init -backend-config="bucket=<your-tf-state-bucket>"

terraform {
  backend "s3" {
    key          = "snaplogic-evaluator/prod/terraform.tfstate"
    region       = "eu-central-1"
    use_lockfile = true # S3-native locking; no DynamoDB lock table needed
  }
}
