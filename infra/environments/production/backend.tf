terraform {
  backend "s3" {
    bucket       = "snaplogic-exercise-evaluator-states" # Same as the value inside state_bucket_name variable in bootstrap/variables.tf
    key          = "production.tfstate"
    region       = "eu-central-1"
    profile      = "evaluator-profile" # Same as the value inside aws_profile variable
    use_lockfile = true                # S3-native locking; no DynamoDB lock table needed
    encrypt      = true
  }
}
