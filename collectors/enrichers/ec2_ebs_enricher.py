"""
EC2→EBS Relationship Enricher.

Reads EC2 Resource Nodes from DynamoDB, extracts attached EBS volume IDs
from the stored BlockDeviceMappings metadata, and updates only the
Relationships field on each EC2 node.
"""
from typing import Any

from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from aws_resource_collectors.utils.logger import get_logger

logger = get_logger(__name__)


def _query_ec2_nodes(table) -> list[dict[str, Any]]:
    """Return all EC2 Resource Nodes from DynamoDB via the TypeIndex GSI."""
    nodes: list[dict[str, Any]] = []
    last_key = None

    while True:
        kwargs: dict[str, Any] = {
            "IndexName": "TypeIndex",
            "KeyConditionExpression": Key("ResourceType").eq("EC2"),
        }
        if last_key:
            kwargs["ExclusiveStartKey"] = last_key

        response = table.query(**kwargs)
        nodes.extend(response.get("Items", []))
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break

    return nodes


def _build_ebs_relationships(bdm_list: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Convert BlockDeviceMappings metadata into EC2→EBS relationship entries.
    Returns an empty list when no volumes are attached.
    """
    relationships: list[dict[str, Any]] = []
    for bdm in bdm_list:
        volume_id = bdm.get("VolumeId")
        if not volume_id:
            continue
        device_name = bdm.get("DeviceName", "")
        # Root device is typically /dev/xvda or /dev/sda1
        is_root = device_name in ("/dev/xvda", "/dev/sda1", "/dev/sda")
        relationships.append({
            "RelationshipType": "ATTACHED_EBS",
            "TargetId": volume_id,
            "TargetType": "EBS",
            "Attributes": {
                "DeviceName": device_name,
                "IsRootDevice": is_root,
            },
        })
    return relationships


def enrich(table, account_id: str | None = None, region: str | None = None) -> int:
    """
    Enrich EC2 nodes with EC2→EBS relationships.

    Reads EC2 nodes from DynamoDB, builds relationship entries from stored
    BlockDeviceMappings, and updates only the Relationships field via a
    DynamoDB update expression.

    Parameters
    ----------
    table : DynamoDB Table resource
    account_id : optional filter — only enrich nodes for this account
    region : optional filter — only enrich nodes for this region

    Returns the number of EC2 nodes updated.
    """
    ec2_nodes = _query_ec2_nodes(table)
    updated = 0

    for node in ec2_nodes:
        node_account = node.get("AccountId")
        node_region = node.get("Region")

        if account_id and node_account != account_id:
            continue
        if region and node_region != region:
            continue

        bdm_list = node.get("Metadata", {}).get("BlockDeviceMappings", [])
        relationships = _build_ebs_relationships(bdm_list)

        try:
            table.update_item(
                Key={
                    "ResourceId": node["ResourceId"],
                    "ResourceType": node["ResourceType"],
                },
                UpdateExpression="SET Relationships = list_append(if_not_exists(Relationships, :empty), :rels)",
                ExpressionAttributeValues={":rels": relationships, ":empty": []},
            )
            updated += 1
            logger.info(
                "EC2→EBS relationships updated",
                extra={
                    "account_id": node_account,
                    "region": node_region,
                    "resource_id": node["ResourceId"],
                    "relationship_count": len(relationships),
                },
            )
        except ClientError as exc:
            logger.error(
                "Failed to update EC2→EBS relationships",
                extra={
                    "account_id": node_account,
                    "region": node_region,
                    "resource_id": node.get("ResourceId"),
                    "error": str(exc),
                },
            )

    logger.info(
        "EC2→EBS enrichment complete",
        extra={"updated": updated},
    )
    return updated
