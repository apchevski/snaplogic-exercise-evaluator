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
    # Batch grading stashes its scratch under jobs/<id>/ and deletes it after
    # the collect step finalizes the report. Delete is scoped to that prefix.
    sid       = "S3DeleteScratch"
    actions   = ["s3:DeleteObject"]
    resources = ["${var.bucket_arn}/jobs/*"]
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
      # Self-redrive: a full-run grade job re-enqueues delayed "poll the batch"
      # messages to its own queue until the async grading batch ends.
      "sqs:SendMessage",
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
      TABLE_NAME  = var.table_name
      DATA_BUCKET = var.bucket_name
      SECRET_ARN  = var.secret_arn
      JUDGE_MODEL = var.judge_model
      # Full-run grade jobs re-enqueue delayed batch-poll messages to this queue.
      QUEUE_URL               = aws_sqs_queue.jobs.url
      EVALUATOR_EXERCISES_DIR = "/tmp/evaluator/exercises"
      EVALUATOR_TMP_DIR       = "/tmp/evaluator/scratch"
      EVALUATOR_GRADES_DIR    = "/tmp/evaluator/grades"
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

# --- Ops alarms ($0: CloudWatch's free tier includes 10 alarms; SNS email ---
# --- notifications are free at this volume) ---------------------------------

resource "aws_sns_topic" "alerts" {
  name = "${var.name_prefix}-ops-alerts"
  tags = var.tags
}

resource "aws_sns_topic_subscription" "alerts_email" {
  count     = var.alert_email == "" ? 0 : 1
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

# A message in the DLQ means a job died so hard it never recorded its own
# failure on the JOB row — the UI shows nothing, so without this alarm the
# only way to notice is a mentor complaining. Paid jobs are never auto-
# retried (maxReceiveCount 1), so a human must look before re-clicking Grade.
resource "aws_cloudwatch_metric_alarm" "dlq_not_empty" {
  alarm_name          = "${var.name_prefix}-dlq-not-empty"
  alarm_description   = "A grade/sync job message landed in the dead-letter queue."
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  dimensions          = { QueueName = aws_sqs_queue.dlq.name }
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]
  tags                = var.tags
}

resource "aws_cloudwatch_metric_alarm" "worker_errors" {
  alarm_name          = "${var.name_prefix}-worker-errors"
  alarm_description   = "The worker Lambda raised an unhandled error (outside the per-job try/except that records failures on the JOB row)."
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = aws_lambda_function.worker.function_name }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  tags                = var.tags
}
