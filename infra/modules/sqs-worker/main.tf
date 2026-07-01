# Worker: SQS job queue (+DLQ, never auto-retry a paid job) and the worker
# Lambda running the shared container image with the worker CMD override.

# --- Queue + DLQ ------------------------------------------------------------

resource "aws_sqs_queue" "dlq" {
  name                      = "${var.name_prefix}-jobs-dlq"
  message_retention_seconds = 14 * 24 * 3600
  tags                      = var.tags
}

resource "aws_sqs_queue" "jobs" {
  name = "${var.name_prefix}-jobs"
  # Must exceed the Lambda timeout (900 s); AWS recommends ~6x.
  visibility_timeout_seconds = 5400
  message_retention_seconds  = 24 * 3600

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dlq.arn
    # 1 = a failed/timed-out grade job is NEVER retried automatically —
    # retries cost real Claude API money; the human re-clicks Grade.
    maxReceiveCount = 1
  })

  tags = var.tags
}

# --- IAM (least privilege) --------------------------------------------------

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "worker" {
  name               = "${var.name_prefix}-worker"
  assume_role_policy = data.aws_iam_policy_document.assume.json
  tags               = var.tags
}

data "aws_iam_policy_document" "worker" {
  statement {
    sid = "Dynamo"
    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
      "dynamodb:DeleteItem",
      "dynamodb:Query",
    ]
    resources = [var.table_arn, "${var.table_arn}/index/*"]
  }
  statement {
    sid       = "S3Objects"
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["${var.bucket_arn}/*"]
  }
  statement {
    sid       = "S3List"
    actions   = ["s3:ListBucket"]
    resources = [var.bucket_arn]
  }
  statement {
    sid       = "Secret"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [var.secret_arn]
  }
  statement {
    sid = "QueueConsume"
    actions = [
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage",
      "sqs:GetQueueAttributes",
    ]
    resources = [aws_sqs_queue.jobs.arn]
  }
  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.worker.arn}:*"]
  }
}

resource "aws_iam_role_policy" "worker" {
  name   = "worker"
  role   = aws_iam_role.worker.id
  policy = data.aws_iam_policy_document.worker.json
}

# --- Lambda -----------------------------------------------------------------

resource "aws_cloudwatch_log_group" "worker" {
  name              = "/aws/lambda/${var.name_prefix}-worker"
  retention_in_days = var.log_retention_days
  tags              = var.tags
}

resource "aws_lambda_function" "worker" {
  function_name = "${var.name_prefix}-worker"
  role          = aws_iam_role.worker.arn
  package_type  = "Image"
  image_uri     = var.image_uri
  timeout       = 900
  memory_size   = 1024
  # reserved_concurrent_executions = 1 One job at a time: serializes SnapLogic + Claude usage and makes the per-student/per-slug locks effectively global.

  image_config {
    command = ["backend.src.worker.handler"]
  }

  environment {
    variables = {
      TABLE_NAME                   = var.table_name
      DATA_BUCKET                  = var.bucket_name
      SECRET_ARN                   = var.secret_arn
      JUDGE_MODEL                  = var.judge_model
      EVALUATOR_EXERCISES_DIR      = "/tmp/evaluator/exercises"
      EVALUATOR_TMP_DIR            = "/tmp/evaluator/scratch"
      EVALUATOR_GRADES_DIR         = "/tmp/evaluator/grades"
      EVALUATOR_DISABLE_UI_REBUILD = "1"
    }
  }

  depends_on = [aws_cloudwatch_log_group.worker]
  tags       = var.tags

  # deploy-backend.yml owns code deploys: it repoints the live function to an
  # immutable commit-SHA tag via `update-function-code`, while Terraform holds
  # the desired image at `:latest`. Ignore image_uri so the two pipelines stop
  # fighting and infra plans don't perpetually show this drift.
  lifecycle {
    ignore_changes = [image_uri]
  }
}

resource "aws_lambda_event_source_mapping" "jobs" {
  event_source_arn = aws_sqs_queue.jobs.arn
  function_name    = aws_lambda_function.worker.arn
  batch_size       = 1
}
