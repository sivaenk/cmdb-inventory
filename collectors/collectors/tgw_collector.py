"""
Transit Gateway collector.
"""
import boto3
from botocore.exceptions import ClientError

from aws_resource_collectors.models.resource_node import ResourceNode
from aws_resource_collectors.utils.logger import get_logger

logger = get_logger(__name__)


def collect(session: boto3.Session, account_id: str, region: str) -> list[ResourceNode]:
    """
    Collect all Transit Gateways and their attachments, returning ResourceNode list.
    Attachments are stored as metadata on the TGW record.
    """
    ec2 = session.client("ec2", region_name=region)
    nodes: list[ResourceNode] = []

    try:
        # Fetch all TGWs
        tgw_paginator = ec2.get_paginator("describe_transit_gateways")
        tgws: list[dict] = []
        for page in tgw_paginator.paginate():
            tgws.extend(page.get("TransitGateways", []))

        # Fetch all TGW attachments and group by TGW ID
        attachments_by_tgw: dict[str, list[dict]] = {}
        try:
            att_paginator = ec2.get_paginator("describe_transit_gateway_attachments")
            for page in att_paginator.paginate():
                for att in page.get("TransitGatewayAttachments", []):
                    tgw_id = att.get("TransitGatewayId", "")
                    attachments_by_tgw.setdefault(tgw_id, []).append({
                        "AttachmentId": att.get("TransitGatewayAttachmentId"),
                        "ResourceType": att.get("ResourceType"),
                        "ResourceId": att.get("ResourceId"),
                        "State": att.get("State"),
                    })
        except ClientError as exc:
            logger.warning(
                "Failed to describe TGW attachments",
                extra={"account_id": account_id, "region": region, "error": str(exc)},
            )

        for tgw in tgws:
            tgw_id = tgw["TransitGatewayId"]
            tags = {t["Key"]: t["Value"] for t in tgw.get("Tags", [])}
            options = tgw.get("Options", {})

            metadata = {
                "State": tgw.get("State"),
                "OwnerId": tgw.get("OwnerId"),
                "Description": tgw.get("Description"),
                "AmazonSideAsn": options.get("AmazonSideAsn"),
                "Attachments": attachments_by_tgw.get(tgw_id, []),
                "Tags": tags,
            }

            nodes.append(
                ResourceNode(
                    ResourceId=tgw_id,
                    ResourceType="TGW",
                    AccountId=account_id,
                    Region=region,
                    Metadata=metadata,
                    Relationships=[],
                )
            )

        logger.info(
            "TGW collection complete",
            extra={"account_id": account_id, "region": region, "count": len(nodes)},
        )

    except ClientError as exc:
        logger.error(
            "TGW collection failed",
            extra={"account_id": account_id, "region": region, "error": str(exc)},
        )

    return nodes
