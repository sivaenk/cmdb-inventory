terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.70.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.0"
    }
  }

  # Uncomment for remote state
  # backend "s3" {
  #   bucket = "your-terraform-state-bucket"
  #   key    = "cmdb/terraform.tfstate"
  #   region = "us-east-1"
  # }
}

provider "aws" {
  region = var.aws_region
}

# -----------------------------------------------------------------------------
# DynamoDB Table
# -----------------------------------------------------------------------------
resource "aws_dynamodb_table" "cmdb" {
  name         = var.dynamodb_table_name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "ResourceId"
  range_key    = "ResourceType"

  attribute {
    name = "ResourceId"
    type = "S"
  }

  attribute {
    name = "ResourceType"
    type = "S"
  }

  attribute {
    name = "AccountId"
    type = "S"
  }

  attribute {
    name = "Region"
    type = "S"
  }

  global_secondary_index {
    name            = "TypeIndex"
    hash_key        = "ResourceType"
    projection_type = "ALL"
  }

  global_secondary_index {
    name            = "AccountIndex"
    hash_key        = "AccountId"
    range_key       = "Region"
    projection_type = "ALL"
  }

  tags = var.tags
}

# -----------------------------------------------------------------------------
# S3 Bucket for Analytical Store
# -----------------------------------------------------------------------------
resource "aws_s3_bucket" "cmdb" {
  bucket = var.s3_bucket_name

  tags = var.tags
}

resource "aws_s3_bucket_versioning" "cmdb" {
  bucket = aws_s3_bucket.cmdb.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "cmdb" {
  bucket = aws_s3_bucket.cmdb.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "cmdb" {
  bucket = aws_s3_bucket.cmdb.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# -----------------------------------------------------------------------------
# Lambda Function
# -----------------------------------------------------------------------------
resource "null_resource" "prepare_lambda" {
  triggers = {
    always_run = timestamp()
  }

  provisioner "local-exec" {
    command = <<-EOT
      rm -rf ${path.module}/lambda_package
      mkdir -p ${path.module}/lambda_package
      cp ${path.module}/../orchestrator.py ${path.module}/lambda_package/
      cp -r ${path.module}/../store ${path.module}/lambda_package/
      cp -r ${path.module}/../collectors ${path.module}/lambda_package/aws_resource_collectors
    EOT
  }
}

data "archive_file" "lambda" {
  type        = "zip"
  source_dir  = "${path.module}/lambda_package"
  output_path = "${path.module}/lambda.zip"

  depends_on = [null_resource.prepare_lambda]
}

resource "aws_lambda_function" "orchestrator" {
  filename         = data.archive_file.lambda.output_path
  function_name    = var.lambda_function_name
  role             = aws_iam_role.lambda.arn
  handler          = "orchestrator.lambda_handler"
  source_code_hash = data.archive_file.lambda.output_base64sha256
  runtime          = "python3.11"
  timeout          = 900
  memory_size      = 1024

  environment {
    variables = {
      SCAN_REGIONS            = join(",", var.scan_regions)
      CROSS_ACCOUNT_ROLE_NAME = var.cross_account_role_name
      DYNAMODB_TABLE_NAME     = aws_dynamodb_table.cmdb.name
      S3_BUCKET_NAME          = aws_s3_bucket.cmdb.id
      CMDB_EXTERNAL_ID        = var.external_id
      RUN_ENRICHMENT          = var.run_enrichment ? "true" : "false"
      TARGET_ACCOUNT_IDS      = join(",", var.target_account_ids)
    }
  }

  tags = var.tags
}

# -----------------------------------------------------------------------------
# IAM Role for Lambda
# -----------------------------------------------------------------------------
resource "aws_iam_role" "lambda" {
  name = "${var.lambda_function_name}-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = {
        Service = "lambda.amazonaws.com"
      }
    }]
  })

  tags = var.tags
}

resource "aws_iam_role_policy" "lambda" {
  name = "${var.lambda_function_name}-policy"
  role = aws_iam_role.lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "arn:aws:logs:*:*:*"
      },
      {
        Effect   = "Allow"
        Action   = ["organizations:ListAccounts"]
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = ["sts:AssumeRole"]
        Resource = "arn:aws:iam::*:role/${var.cross_account_role_name}"
      },
      {
        Effect = "Allow"
        Action = [
          "dynamodb:PutItem",
          "dynamodb:UpdateItem",
          "dynamodb:GetItem",
          "dynamodb:Query",
          "dynamodb:Scan"
        ]
        Resource = [
          aws_dynamodb_table.cmdb.arn,
          "${aws_dynamodb_table.cmdb.arn}/index/*"
        ]
      },
      {
        Effect   = "Allow"
        Action   = ["s3:PutObject"]
        Resource = "${aws_s3_bucket.cmdb.arn}/*"
      },
      {
        Sid    = "LocalAccountReadOnly"
        Effect = "Allow"
        Action = [
          "ec2:DescribeInstances",
          "ec2:DescribeVolumes",
          "ec2:DescribeVpcs",
          "ec2:DescribeAddresses",
          "ec2:DescribeTransitGateways",
          "ec2:DescribeTransitGatewayAttachments",
          "ec2:DescribeRouteTables",
          "ec2:DescribeSecurityGroups",
          "ec2:DescribeImages",
          "rds:DescribeDBInstances",
          "elasticloadbalancing:DescribeLoadBalancers",
          "elasticloadbalancing:DescribeTargetGroups",
          "elasticloadbalancing:DescribeTargetHealth",
          "elasticloadbalancing:DescribeInstanceHealth",
          "elasticloadbalancing:DescribeTags",
          "elasticfilesystem:DescribeFileSystems",
          "s3:ListAllMyBuckets",
          "s3:GetBucketLocation",
          "s3:GetBucketVersioning",
          "s3:GetBucketPublicAccessBlock"
        ]
        Resource = "*"
      }
    ]
  })
}

# -----------------------------------------------------------------------------
# EventBridge Schedule
# -----------------------------------------------------------------------------
resource "aws_cloudwatch_event_rule" "schedule" {
  count               = var.enable_schedule ? 1 : 0
  name                = "${var.lambda_function_name}-schedule"
  schedule_expression = var.schedule_expression

  tags = var.tags
}

resource "aws_cloudwatch_event_target" "lambda" {
  count     = var.enable_schedule ? 1 : 0
  rule      = aws_cloudwatch_event_rule.schedule[0].name
  target_id = "InvokeLambda"
  arn       = aws_lambda_function.orchestrator.arn
}

resource "aws_lambda_permission" "eventbridge" {
  count         = var.enable_schedule ? 1 : 0
  statement_id  = "AllowEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.orchestrator.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.schedule[0].arn
}

# -----------------------------------------------------------------------------
# Outputs
# -----------------------------------------------------------------------------
output "dynamodb_table_name" {
  value       = aws_dynamodb_table.cmdb.name
  description = "Name of the DynamoDB table storing CMDB data"
}

output "s3_bucket_name" {
  value       = aws_s3_bucket.cmdb.id
  description = "Name of the S3 bucket for analytical data"
}

output "lambda_function_name" {
  value       = aws_lambda_function.orchestrator.function_name
  description = "Name of the Lambda orchestrator function"
}

output "lambda_role_arn" {
  value       = aws_iam_role.lambda.arn
  description = "ARN of the Lambda execution role"
}
