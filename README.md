# Dyson Cloud Ops - AWS CMDB Inventory

Multi-account AWS resource inventory collector with DynamoDB and S3 storage.

## Features

- **Multi-account scanning** via AWS Organizations
- **Resource types**: EC2, EBS, RDS, VPC, S3, EIP, EFS, Load Balancers, Transit Gateways, Route Tables
- **Dual storage**: DynamoDB (operational) + S3 (analytical/historical)
- **Scheduled scans**: Daily via EventBridge
- **Relationship enrichment**: EC2→EBS, EC2→RDS, EC2→LB mappings

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    Management Account                            │
│  ┌─────────────┐    ┌──────────────┐    ┌───────────────────┐  │
│  │ EventBridge │───▶│    Lambda    │───▶│ DynamoDB + S3     │  │
│  │  (Daily)    │    │ Orchestrator │    │ (CMDB Storage)    │  │
│  └─────────────┘    └──────┬───────┘    └───────────────────┘  │
│                            │                                     │
│                            │ AssumeRole                          │
└────────────────────────────┼────────────────────────────────────┘
                             │
         ┌───────────────────┼───────────────────┐
         ▼                   ▼                   ▼
┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐
│  Member Acct 1  │ │  Member Acct 2  │ │  Member Acct N  │
│ CMDBInventory   │ │ CMDBInventory   │ │ CMDBInventory   │
│     Role        │ │     Role        │ │     Role        │
└─────────────────┘ └─────────────────┘ └─────────────────┘
```

## Prerequisites

- AWS CLI v2 configured with management account credentials
- Terraform >= 1.5.0
- Python 3.11+ (for local testing)
- AWS Organizations enabled with member accounts

## Quick Start

### Step 1: Configure Terraform

```bash
cd infra

# Copy example config
cp terraform.tfvars.example terraform.tfvars

# Edit with your values
vim terraform.tfvars
```

Required configuration:
```hcl
s3_bucket_name = "cmdb-inventory-YOUR_ACCOUNT_ID"  # Must be globally unique
scan_regions   = ["us-east-1", "us-west-2"]        # Regions to scan
```

### Step 2: Deploy Infrastructure

```bash
# Set your AWS profile
export AWS_PROFILE=your-management-account-profile

# Initialize Terraform
terraform init

# Review changes
terraform plan

# Deploy
terraform apply
```

### Step 3: Deploy Cross-Account Role to Member Accounts

Use CloudFormation StackSets to deploy the IAM role to all member accounts:

```bash
# Get your Organization root ID
ORG_ROOT=$(aws organizations list-roots --query 'Roots[0].Id' --output text)
MGMT_ACCOUNT=$(aws sts get-caller-identity --query 'Account' --output text)

# Create StackSet
aws cloudformation create-stack-set \
  --stack-set-name CMDBInventoryRole \
  --template-body file://cross_account_role.yaml \
  --parameters ParameterKey=ManagementAccountId,ParameterValue=$MGMT_ACCOUNT \
  --capabilities CAPABILITY_NAMED_IAM \
  --permission-model SERVICE_MANAGED \
  --auto-deployment Enabled=true,RetainStacksOnAccountRemoval=false

# Deploy to all accounts in the organization
aws cloudformation create-stack-instances \
  --stack-set-name CMDBInventoryRole \
  --deployment-targets OrganizationalUnitIds=$ORG_ROOT \
  --regions us-east-1 \
  --operation-preferences FailureToleranceCount=0,MaxConcurrentCount=5

# Check deployment status
aws cloudformation list-stack-instances \
  --stack-set-name CMDBInventoryRole \
  --query 'Summaries[*].[Account,Region,Status]' \
  --output table
```

### Step 4: Run Initial Scan

```bash
# Invoke Lambda manually
aws lambda invoke \
  --function-name cmdb-orchestrator \
  --invocation-type Event \
  /tmp/output.json

# Check results after ~2 minutes
aws dynamodb scan \
  --table-name CMDBInventory \
  --select COUNT
```

## Usage

### Query Resources by Type

```bash
aws dynamodb query \
  --table-name CMDBInventory \
  --index-name TypeIndex \
  --key-condition-expression "ResourceType = :type" \
  --expression-attribute-values '{":type":{"S":"EC2"}}' \
  --output table
```

### Query Resources by Account

```bash
aws dynamodb query \
  --table-name CMDBInventory \
  --index-name AccountIndex \
  --key-condition-expression "AccountId = :acct" \
  --expression-attribute-values '{":acct":{"S":"123456789012"}}' \
  --output table
```

### Export to CSV

```bash
aws dynamodb scan --table-name CMDBInventory --output json > cmdb_data.json

python3 -c "
import json, csv
data = json.load(open('cmdb_data.json'))
with open('cmdb_report.csv', 'w') as f:
    w = csv.writer(f)
    w.writerow(['ResourceId','ResourceType','AccountId','Region'])
    for item in data['Items']:
        if item.get('ResourceType',{}).get('S') != 'SCAN_METADATA':
            w.writerow([
                item.get('ResourceId',{}).get('S',''),
                item.get('ResourceType',{}).get('S',''),
                item.get('AccountId',{}).get('S',''),
                item.get('Region',{}).get('S','')
            ])
print('Exported to cmdb_report.csv')
"
```

## Configuration Options

| Variable | Description | Default |
|----------|-------------|---------|
| `aws_region` | Deployment region | `us-east-1` |
| `s3_bucket_name` | S3 bucket name (required) | - |
| `scan_regions` | Regions to scan | `["us-east-1"]` |
| `enable_schedule` | Enable daily scans | `true` |
| `schedule_expression` | Cron for scheduled scans | `cron(0 6 * * ? *)` |
| `run_enrichment` | Enable relationship mapping | `false` |

## Resource Types Collected

| Type | AWS Service | Attributes |
|------|-------------|------------|
| EC2 | EC2 Instances | Instance ID, Type, State, VPC, Subnet, Security Groups |
| EBS | EBS Volumes | Volume ID, Size, Type, State, Attachments |
| RDS | RDS Instances | DB ID, Engine, Class, Status, VPC |
| VPC | VPCs | VPC ID, CIDR, State |
| S3 | S3 Buckets | Bucket name, Region, Versioning, Encryption |
| EIP | Elastic IPs | Allocation ID, Public IP, Association |
| EFS | EFS File Systems | File System ID, Size, State |
| LB | Load Balancers | ARN, Type, Scheme, VPC |
| TGW | Transit Gateways | TGW ID, State, Attachments |
| RouteTable | Route Tables | Route Table ID, VPC, Routes |

## Cleanup

```bash
# Remove StackSet instances first
aws cloudformation delete-stack-instances \
  --stack-set-name CMDBInventoryRole \
  --deployment-targets OrganizationalUnitIds=$ORG_ROOT \
  --regions us-east-1 \
  --no-retain-stacks

# Wait for deletion, then remove StackSet
aws cloudformation delete-stack-set --stack-set-name CMDBInventoryRole

# Destroy Terraform resources
cd infra
terraform destroy
```

## License

MIT
