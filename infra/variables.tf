variable "aws_region" {
  description = "AWS region for deployment"
  type        = string
  default     = "us-east-1"
}

variable "dynamodb_table_name" {
  description = "Name of the DynamoDB CMDB table"
  type        = string
  default     = "CMDBInventory"
}

variable "s3_bucket_name" {
  description = "Name of the S3 bucket for analytical store (must be globally unique)"
  type        = string
}

variable "lambda_function_name" {
  description = "Name of the Lambda function"
  type        = string
  default     = "cmdb-orchestrator"
}

variable "cross_account_role_name" {
  description = "Name of the cross-account IAM role to assume in member accounts"
  type        = string
  default     = "CMDBInventoryRole"
}

variable "external_id" {
  description = "ExternalId for cross-account role assumption (optional but recommended)"
  type        = string
  default     = ""
  sensitive   = true
}

variable "scan_regions" {
  description = "List of AWS regions to scan for resources"
  type        = list(string)
  default     = ["us-east-1"]
}

variable "run_enrichment" {
  description = "Whether to run relationship enrichment after collection"
  type        = bool
  default     = false
}

variable "enable_schedule" {
  description = "Enable EventBridge schedule for automatic daily scans"
  type        = bool
  default     = true
}

variable "schedule_expression" {
  description = "Cron expression for scheduled scans"
  type        = string
  default     = "cron(0 6 * * ? *)" # Daily at 6 AM UTC
}

variable "tags" {
  description = "Tags to apply to all resources"
  type        = map(string)
  default = {
    Project   = "CMDB"
    ManagedBy = "Terraform"
  }
}
