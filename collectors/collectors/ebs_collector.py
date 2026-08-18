"""
EBS Volume collector.
"""
import boto3
from botocore.exceptions import ClientError

from aws_resource_collectors.models.resource_node import ResourceNode
from aws_resource_collectors.utils.logger import get_logger

logger = get_logger(__name__)


def collect(session: boto3.Session, account_id: str, region: str) -> list[ResourceNode]:
    """
    Collect all EBS volumes and return ResourceNode list.
    Marks unattached volumes with AttachmentState = "unattached".
    """
    ec2 = session.client("ec2", region_name=region)
    nodes: list[ResourceNode] = []

    try:
        paginator = ec2.get_paginator("describe_volumes")

        for page in paginator.paginate():
            for vol in page.get("Volumes", []):
                volume_id = vol["VolumeId"]
                tags = {t["Key"]: t["Value"] for t in vol.get("Tags", [])}

                attachments = vol.get("Attachments", [])
                if attachments:
                    attached_instance_id = attachments[0].get("InstanceId")
                    attachment_state = attachments[0].get("State", "attached")
                else:
                    attached_instance_id = None
                    attachment_state = "unattached"

                metadata = {
                    "VolumeType": vol.get("VolumeType"),
                    "SizeGiB": vol.get("Size"),
                    "Iops": vol.get("Iops"),
                    "ThroughputMiBs": vol.get("Throughput"),
                    "State": vol.get("State"),
                    "Encrypted": vol.get("Encrypted"),
                    "AvailabilityZone": vol.get("AvailabilityZone"),
                    "AttachedInstanceId": attached_instance_id,
                    "AttachmentState": attachment_state,
                    "Tags": tags,
                }

                nodes.append(
                    ResourceNode(
                        ResourceId=volume_id,
                        ResourceType="EBS",
                        AccountId=account_id,
                        Region=region,
                        Metadata=metadata,
                        Relationships=[],
                    )
                )

        logger.info(
            "EBS collection complete",
            extra={"account_id": account_id, "region": region, "count": len(nodes)},
        )

    except ClientError as exc:
        logger.error(
            "EBS collection failed",
            extra={"account_id": account_id, "region": region, "error": str(exc)},
        )

    return nodes
