"""
VPC collector.
"""
import boto3
from botocore.exceptions import ClientError

from aws_resource_collectors.models.resource_node import ResourceNode
from aws_resource_collectors.utils.logger import get_logger

logger = get_logger(__name__)


def collect(session: boto3.Session, account_id: str, region: str) -> list[ResourceNode]:
    """
    Collect all VPCs and return ResourceNode list.
    """
    ec2 = session.client("ec2", region_name=region)
    nodes: list[ResourceNode] = []

    try:
        paginator = ec2.get_paginator("describe_vpcs")

        for page in paginator.paginate():
            for vpc in page.get("Vpcs", []):
                vpc_id = vpc["VpcId"]
                tags = {t["Key"]: t["Value"] for t in vpc.get("Tags", [])}

                metadata = {
                    "CidrBlock": vpc.get("CidrBlock"),
                    "State": vpc.get("State"),
                    "IsDefault": vpc.get("IsDefault"),
                    "DhcpOptionsId": vpc.get("DhcpOptionsId"),
                    "Tags": tags,
                }

                nodes.append(
                    ResourceNode(
                        ResourceId=vpc_id,
                        ResourceType="VPC",
                        AccountId=account_id,
                        Region=region,
                        Metadata=metadata,
                        Relationships=[],
                    )
                )

        logger.info(
            "VPC collection complete",
            extra={"account_id": account_id, "region": region, "count": len(nodes)},
        )

    except ClientError as exc:
        logger.error(
            "VPC collection failed",
            extra={"account_id": account_id, "region": region, "error": str(exc)},
        )

    return nodes
