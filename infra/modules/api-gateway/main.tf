# HTTP API: JWT authorizer (Cognito) on every route + the API Lambda (same
# container image as the worker, default CMD = backend.src.api.handler).

# --- Lambda ----------------------------------------------------------------

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "api" {
  name               = "${var.name_prefix}-api"
  assume_role_policy = data.aws_iam_policy_document.assume.json
  tags               = var.tags
}

data "aws_iam_policy_document" "api" {
  statement {
    sid = "Dynamo"
    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      # PATCH /v1/students/{slug}/report stamps the edit onto the student card
      # with update_item after rewriting report.json in S3.
      "dynamodb:UpdateItem",
      "dynamodb:DeleteItem",
      "dynamodb:Query",
    ]
    resources = [var.table_arn, "${var.table_arn}/index/*"]
  }
  statement {
    sid       = "S3ReadReports"
    actions   = ["s3:GetObject"]
    resources = ["${var.bucket_arn}/*"]
  }
  # Lazy mirror of baked-in student input files (exercises/<slug>/resources/)
  # so downloads are presigned S3 GETs instead of Lambda-streamed bodies.
  # Scoped to its own prefix: the API must NOT be able to overwrite the
  # worker-owned artifacts under exercises/.
  statement {
    sid       = "S3WriteExerciseResources"
    actions   = ["s3:PutObject"]
    resources = ["${var.bucket_arn}/exercise-resources/*"]
  }
  # PATCH /v1/students/{slug}/report edits AI-written report text in place.
  # Only the two report files themselves are writable — everything else
  # under students/ stays worker-only.
  statement {
    sid     = "S3EditStoredReports"
    actions = ["s3:PutObject"]
    resources = [
      "${var.bucket_arn}/students/*/report.json",
      "${var.bucket_arn}/students/*/report.md",
    ]
  }
  # POST/PUT /v1/exercises author exercises straight into S3 — the canonical
  # home of authored content. Only the authored filenames are writable, and
  # only input files are deletable — the generated artifacts sharing the
  # exercises/ prefix (task.json, solution.json, expected/) stay worker-only.
  statement {
    sid     = "S3WriteAuthoredExercises"
    actions = ["s3:PutObject"]
    resources = [
      "${var.bucket_arn}/exercises/*/description.md",
      "${var.bucket_arn}/exercises/*/notes.md",
      "${var.bucket_arn}/exercises/*/resources/*",
    ]
  }
  # PUT /v1/exercises removes single input files; DELETE /v1/students/{slug}
  # and DELETE /v1/exercises/{slug} purge every object VERSION under the
  # entity's prefixes — the bucket is versioned, and a hard delete must
  # leave nothing recoverable behind.
  statement {
    sid     = "S3HardDelete"
    actions = ["s3:DeleteObject", "s3:DeleteObjectVersion"]
    resources = [
      "${var.bucket_arn}/students/*",
      "${var.bucket_arn}/exercises/*",
      "${var.bucket_arn}/exercise-resources/*",
    ]
  }
  # ListObjectVersions for the purge (authorized by s3:ListBucketVersions on
  # the bucket). students/* becomes enumerable here — unavoidable: a student
  # delete has to find every report version it must remove.
  statement {
    sid       = "S3ListVersionsForHardDelete"
    actions   = ["s3:ListBucketVersions"]
    resources = [var.bucket_arn]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["students/*", "exercises/*", "exercise-resources/*"]
    }
  }
  # Without ListBucket, S3 answers HeadObject on a missing key with 403
  # instead of 404, which broke the first download of every resource file.
  # Prefix-scoped so the API still can't enumerate other prefixes (reports
  # under students/ stay unlistable). exercises/* is listable so the API can
  # discover UI-authored exercises and their input files.
  statement {
    sid       = "S3HeadExerciseResources"
    actions   = ["s3:ListBucket"]
    resources = [var.bucket_arn]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["exercise-resources/*", "exercises/*"]
    }
  }
  statement {
    sid       = "QueueSend"
    actions   = ["sqs:SendMessage"]
    resources = [var.queue_arn]
  }
  # POST /v1/students with an email invites the student into the pool's
  # read-only `student` group (Cognito emails the temporary password);
  # DELETE /v1/students/{slug} removes that login again ("no tracks left").
  statement {
    sid = "CognitoStudentLogins"
    actions = [
      "cognito-idp:AdminCreateUser",
      "cognito-idp:AdminAddUserToGroup",
      "cognito-idp:AdminDeleteUser",
    ]
    resources = [var.user_pool_arn]
  }
  # POST /v1/students checks the student's SnapLogic project exists before
  # registering; the SnapLogic credentials live in the app secret.
  statement {
    sid       = "Secret"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [var.secret_arn]
  }
  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.api.arn}:*"]
  }
}

resource "aws_iam_role_policy" "api" {
  name   = "api"
  role   = aws_iam_role.api.id
  policy = data.aws_iam_policy_document.api.json
}

resource "aws_cloudwatch_log_group" "api" {
  name              = "/aws/lambda/${var.name_prefix}-api"
  retention_in_days = var.log_retention_days
  tags              = var.tags
}

resource "aws_lambda_function" "api" {
  function_name = "${var.name_prefix}-api"
  role          = aws_iam_role.api.arn
  package_type  = "Image"
  image_uri     = var.image_uri
  timeout       = 29 # API Gateway integration ceiling
  memory_size   = 512

  # Image default CMD is already the api handler; pinned here so the two
  # Lambdas stay correct even if the image default changes.
  image_config {
    command = ["backend.src.api.handler"]
  }

  environment {
    variables = {
      TABLE_NAME    = var.table_name
      DATA_BUCKET   = var.bucket_name
      QUEUE_URL     = var.queue_url
      SECRET_ARN    = var.secret_arn
      ALLOWED_CIDRS = join(",", var.allowed_cidrs)
      USER_POOL_ID  = var.user_pool_id
      JUDGE_MODEL   = var.judge_model
    }
  }

  depends_on = [aws_cloudwatch_log_group.api]
  tags       = var.tags

  # deploy-backend.yml owns code deploys: it repoints the live function to an
  # immutable commit-SHA tag via `update-function-code`, while Terraform holds
  # the desired image at `:latest`. Ignore image_uri so the two pipelines stop
  # fighting and infra plans don't perpetually show this drift.
  lifecycle {
    ignore_changes = [image_uri]
  }
}

# --- HTTP API ----------------------------------------------------------------

resource "aws_apigatewayv2_api" "main" {
  name          = "${var.name_prefix}-api"
  protocol_type = "HTTP"

  cors_configuration {
    allow_origins = var.cors_allow_origins
    allow_methods = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
    allow_headers = ["authorization", "content-type"]
    max_age       = 3600
  }

  tags = var.tags
}

resource "aws_apigatewayv2_authorizer" "jwt" {
  api_id           = aws_apigatewayv2_api.main.id
  authorizer_type  = "JWT"
  identity_sources = ["$request.header.Authorization"]
  name             = "cognito"

  jwt_configuration {
    audience = [var.jwt_audience]
    issuer   = var.jwt_issuer
  }
}

resource "aws_apigatewayv2_integration" "lambda" {
  api_id                 = aws_apigatewayv2_api.main.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.api.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "routes" {
  for_each           = toset(local.routes)
  api_id             = aws_apigatewayv2_api.main.id
  route_key          = each.value
  target             = "integrations/${aws_apigatewayv2_integration.lambda.id}"
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.jwt.id
}

resource "aws_cloudwatch_log_group" "access" {
  name              = "/aws/apigateway/${var.name_prefix}-api"
  retention_in_days = var.log_retention_days
  tags              = var.tags
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.main.id
  name        = "$default"
  auto_deploy = true

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.access.arn
    format = jsonencode({
      requestId = "$context.requestId"
      ip        = "$context.identity.sourceIp"
      method    = "$context.httpMethod"
      path      = "$context.path"
      status    = "$context.status"
      error     = "$context.error.message"
    })
  }

  tags = var.tags
}

resource "aws_lambda_permission" "apigw" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.api.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.main.execution_arn}/*/*"
}
