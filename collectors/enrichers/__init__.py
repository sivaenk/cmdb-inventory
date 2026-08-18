"""
Relationship enrichers — discover connections between resources.

Each enricher module operates on a DynamoDB table containing ResourceNode items,
finding relationships and appending them to the Relationships field.
"""

from aws_resource_collectors.enrichers import (
    ec2_ebs_enricher,
    ec2_lb_enricher,
    ec2_rds_enricher,
)

__all__ = [
    "ec2_ebs_enricher",
    "ec2_lb_enricher",
    "ec2_rds_enricher",
]
