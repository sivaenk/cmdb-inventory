"""
AWS resource collectors — one module per resource type.

Each collector module exposes a `collect(session, account_id, region)` function
that returns a list of ResourceNode objects.
"""

from aws_resource_collectors.collectors import (
    ebs_collector,
    ec2_collector,
    efs_collector,
    eip_collector,
    lb_collector,
    rds_collector,
    route_table_collector,
    s3_collector,
    tgw_collector,
    vpc_collector,
)

__all__ = [
    "ebs_collector",
    "ec2_collector",
    "efs_collector",
    "eip_collector",
    "lb_collector",
    "rds_collector",
    "route_table_collector",
    "s3_collector",
    "tgw_collector",
    "vpc_collector",
]
