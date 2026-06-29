# ECR repository for the single Lambda container image (api + worker share it;
# each Lambda overrides the image CMD).

resource "aws_ecr_repository" "lambda" {
  name                 = "${var.name_prefix}-lambda"
  image_tag_mutability = "MUTABLE" # `latest` is repointed by deploy-backend.yml

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = var.tags
}

# Keep the registry tiny: only the last 10 images survive.
resource "aws_ecr_lifecycle_policy" "lambda" {
  repository = aws_ecr_repository.lambda.name
  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Keep last 10 images"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = 10
        }
        action = {
          type = "expire"
        }
      }
    ]
  })
}
