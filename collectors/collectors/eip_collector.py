"""
Elastic IP (EIP) collector.
"""
import boto3
from botocore.exceptions import ClientError

from aws_resource_collectors.models.resource_node import ResourceNode
from aws_resource_collectors.utils.logger import get_logger

logger = get_logger(__name__)


def collect(session: boto3.Session, account_id: str, region: str) -> list[ResourceNode]:
    """
    Collect all Elastic IPs and return ResourceNode list.
    Marks unassociated EIPs with AssociationState = "unassociated".
    """
    ec2 = session.client("ec2", region_name=region)
    nodes: list[ResourceNode] = []

    try:
        resp = ec2.describe_addresses()

        for addr in resp.get("Addresses", []):
            allocation_id = addr.get("AllocationId") or addr.get("PublicIp")
            tags = {t["Key"]: t["Value"] for t in addr.get("Tags", [])}

            associated_instance = addr.get("InstanceId")
            associated_eni = addr.get("NetworkInterfaceId")
            association_state = "unassociated" if not associated_instance and not associated_eni else "associated"

            metadata = {
                "AllocationId": addr.get("AllocationId"),
                "PublicIp": addr.get("PublicIp"),
                "Domain": addr.get("Domain"),
                "AssociatedInstanceId": associated_instance,
                "AssociatedNetworkInterfaceId": associated_eni,
                "AssociationState": association_state,
                "Tags": tags,
            }

            nodes.append(
                ResourceNode(
                    ResourceId=allocation_id,
                    ResourceType="EIP",
                    AccountId=account_id,
                    Region=region,
                    Metadata=metadata,
                    Relationships=[],
                )
            )

        logger.info(
            "EIP collection complete",
            extra={"account_id": account_id, "region": region, "count": len(nodes)},
        )

    except ClientError as exc:
        logger.error(
            "EIP collection failed",
            extra={"account_id": account_id, "region": region, "error": str(exc)},
        )

    return nodes
