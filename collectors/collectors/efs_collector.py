"""
EFS File System collector.
"""
import boto3
from botocore.exceptions import ClientError

from aws_resource_collectors.models.resource_node import ResourceNode
from aws_resource_collectors.utils.logger import get_logger

logger = get_logger(__name__)


def collect(session: boto3.Session, account_id: str, region: str) -> list[ResourceNode]:
    """
    Collect all EFS file systems and return ResourceNode list.
    """
    efs = session.client("efs", region_name=region)
    nodes: list[ResourceNode] = []

    try:
        paginator = efs.get_paginator("describe_file_systems")

        for page in paginator.paginate():
            for fs in page.get("FileSystems", []):
                fs_id = fs["FileSystemId"]
                tags = {t["Key"]: t["Value"] for t in fs.get("Tags", [])}
                name = tags.get("Name", fs.get("Name", ""))

                size_bytes = fs.get("SizeInBytes", {})

                metadata = {
                    "Name": name,
                    "LifeCycleState": fs.get("LifeCycleState"),
                    "PerformanceMode": fs.get("PerformanceMode"),
                    "ThroughputMode": fs.get("ThroughputMode"),
                    "SizeInBytes": size_bytes.get("Value") if isinstance(size_bytes, dict) else size_bytes,
                    "Encrypted": fs.get("Encrypted"),
                    "Tags": tags,
                }

                nodes.append(
                    ResourceNode(
                        ResourceId=fs_id,
                        ResourceType="EFS",
                        AccountId=account_id,
                        Region=region,
                        Metadata=metadata,
                        Relationships=[],
                    )
                )

        logger.info(
            "EFS collection complete",
            extra={"account_id": account_id, "region": region, "count": len(nodes)},
        )

    except ClientError as exc:
        logger.error(
            "EFS collection failed",
            extra={"account_id": account_id, "region": region, "error": str(exc)},
        )

    return nodes
