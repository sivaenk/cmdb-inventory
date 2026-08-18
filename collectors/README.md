# aws-resource-collectors

Shared Python library for AWS multi-account resource collection. Used by both `cmdb-backend` and `cloud-ops-agent`.

## Installation

```bash
# From the cloud-foundations directory
pip install -e ./aws-resource-collectors

# Or install from path
pip install ./aws-resource-collectors
```

## Usage

```python
import boto3
from aws_resource_collectors.collectors import ec2_collector, rds_collector
from aws_resource_collectors.models import ResourceNode
from aws_resource_collectors.utils.aws_session import assume_role, verify_identity

# Assume role into a member account
session = assume_role(
    account_id="123456789012",
    role_name="CloudOpsReadOnlyRole",
    external_id="your-external-id"
)

# Collect EC2 instances
ec2_nodes = ec2_collector.collect(session, "123456789012", "us-east-1")

# Each node is a ResourceNode dataclass
for node in ec2_nodes:
    print(f"{node.ResourceId}: {node.Metadata.get('InstanceType')}")
```

## Components

### collectors/

Resource-specific AWS API collectors. Each returns `list[ResourceNode]`.

| Collector | Resource | Scope |
|-----------|----------|-------|
| `ec2_collector` | EC2 instances | Per region |
| `ebs_collector` | EBS volumes | Per region |
| `rds_collector` | RDS instances | Per region |
| `lb_collector` | ALB/NLB/CLB/GWLB | Per region |
| `efs_collector` | EFS file systems | Per region |
| `vpc_collector` | VPCs | Per region |
| `eip_collector` | Elastic IPs | Per region |
| `tgw_collector` | Transit Gateways | Per region |
| `route_table_collector` | Route Tables | Per region |
| `s3_collector` | S3 buckets | Global |

### models/

- `ResourceNode` — Core dataclass for all collected resources

### utils/

- `aws_session` — Cross-account role assumption, identity verification
- `logger` — Structured JSON logging

### enrichers/

Relationship discovery between resources:

- `ec2_ebs_enricher` — Links EC2 instances to their EBS volumes
- `ec2_rds_enricher` — Links EC2 instances to RDS via security groups
- `ec2_lb_enricher` — Links EC2 instances to load balancers via target groups

### validation/

Post-collection data quality checks:

- `validate_inventory` — Counts match scan metadata
- `validate_schema` — Required fields present
- `validate_relationships` — Enrichment coverage

## ResourceNode Schema

```python
@dataclass
class ResourceNode:
    ResourceId: str              # e.g., "i-0abc123def456"
    ResourceType: str            # e.g., "EC2", "RDS", "S3"
    AccountId: str               # 12-digit AWS account ID
    Region: str                  # e.g., "us-east-1"
    Metadata: dict[str, Any]     # Resource-specific attributes
    Relationships: list[dict]    # Links to other resources
    DiscoveredAt: str            # ISO timestamp of first discovery
    LastSeenAt: str              # ISO timestamp of last scan
```

## License

MIT
