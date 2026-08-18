"""
EC2→RDS Relationship Enricher.

Reads EC2 and RDS Resource Nodes from DynamoDB grouped by VpcId, calls
ec2:DescribeSecurityGroups to get current inbound rules, and writes
EC2→RDS relationship entries to EC2 nodes where RDS SG inbound rules
permit traffic from the EC2 SG.
"""
from collections import defaultdict
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from aws_resource_collectors.utils.logger import get_logger

logger = get_logger(__name__)


def _query_nodes_by_type(table, resource_type: str) -> list[dict[str, Any]]:
    """Return all Resource Nodes of a given type via the TypeIndex GSI."""
    nodes: list[dict[str, Any]] = []
    last_key = None

    while True:
        kwargs: dict[str, Any] = {
            "IndexName": "TypeIndex",
            "KeyConditionExpression": Key("ResourceType").eq(resource_type),
        }
        if last_key:
            kwargs["ExclusiveStartKey"] = last_key

        response = table.query(**kwargs)
        nodes.extend(response.get("Items", []))
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break

    return nodes


def _describe_security_groups(session: boto3.Session, region: str, sg_ids: list[str]) -> dict[str, dict]:
    """
    Fetch security group rules for the given SG IDs.
    Returns {sg_id: sg_dict} mapping.
    """
    if not sg_ids:
        return {}

    ec2 = session.client("ec2", region_name=region)
    sg_map: dict[str, dict] = {}

    # DescribeSecurityGroups accepts up to 200 IDs per call
    for i in range(0, len(sg_ids), 200):
        batch = list(sg_ids)[i:i + 200]
        try:
            response = ec2.describe_security_groups(GroupIds=batch)
            for sg in response.get("SecurityGroups", []):
                sg_map[sg["GroupId"]] = sg
        except ClientError as exc:
            logger.warning(
                "Failed to describe security groups",
                extra={"region": region, "error": str(exc)},
            )

    return sg_map


def _rds_sg_permits_ec2_sg(rds_sg: dict, ec2_sg_ids: set[str]) -> list[str]:
    """
    Check if any inbound rule on rds_sg references one of the ec2_sg_ids.
    Returns the list of matching EC2 SG IDs (empty if no match).
    """
    matched: list[str] = []
    for rule in rds_sg.get("IpPermissions", []):
        for pair in rule.get("UserIdGroupPairs", []):
            gid = pair.get("GroupId")
            if gid and gid in ec2_sg_ids:
                matched.append(gid)
    return matched


def enrich(table, session: boto3.Session, account_id: str | None = None, region: str | None = None) -> int:
    """
    Enrich EC2 nodes with EC2→RDS relationships inferred from SG analysis.

    For each EC2-RDS pair in the same VPC, checks whether any RDS security
    group inbound rule permits traffic from an EC2 security group.
    Updates only the Relationships field on matching EC2 nodes.

    Parameters
    ----------
    table : DynamoDB Table resource
    session : boto3.Session for calling ec2:DescribeSecurityGroups
    account_id : optional filter
    region : optional filter

    Returns the number of EC2 nodes updated.
    """
    ec2_nodes = _query_nodes_by_type(table, "EC2")
    rds_nodes = _query_nodes_by_type(table, "RDS")

    # Apply optional filters
    if account_id:
        ec2_nodes = [n for n in ec2_nodes if n.get("AccountId") == account_id]
        rds_nodes = [n for n in rds_nodes if n.get("AccountId") == account_id]
    if region:
        ec2_nodes = [n for n in ec2_nodes if n.get("Region") == region]
        rds_nodes = [n for n in rds_nodes if n.get("Region") == region]

    if not ec2_nodes or not rds_nodes:
        logger.info("No EC2 or RDS nodes to enrich", extra={"account_id": account_id, "region": region})
        return 0

    # Collect SG IDs per region — SGs are region-scoped so must be resolved per region
    sg_ids_by_region: dict[str, set[str]] = defaultdict(set)
    for node in ec2_nodes:
        node_region = node.get("Region", "us-east-1")
        sg_ids_by_region[node_region].update(node.get("Metadata", {}).get("SecurityGroupIds", []))
    for node in rds_nodes:
        node_region = node.get("Region", "us-east-1")
        sg_ids_by_region[node_region].update(node.get("Metadata", {}).get("VpcSecurityGroupIds", []))

    # Build a unified sg_map by calling DescribeSecurityGroups once per region
    sg_map: dict[str, dict] = {}
    for r, sg_ids in sg_ids_by_region.items():
        sg_map.update(_describe_security_groups(session, r, list(sg_ids)))

    # Group RDS nodes by VpcId for efficient lookup
    rds_by_vpc: dict[str, list[dict]] = defaultdict(list)
    for rds in rds_nodes:
        vpc_id = rds.get("Metadata", {}).get("VpcId")
        if vpc_id:
            rds_by_vpc[vpc_id].append(rds)

    updated = 0

    for ec2 in ec2_nodes:
        vpc_id = ec2.get("Metadata", {}).get("VpcId")
        ec2_sg_ids = set(ec2.get("Metadata", {}).get("SecurityGroupIds", []))
        rds_candidates = rds_by_vpc.get(vpc_id, [])

        relationships: list[dict[str, Any]] = []

        for rds in rds_candidates:
            rds_sg_ids = rds.get("Metadata", {}).get("VpcSecurityGroupIds", [])
            matched_sgs: list[str] = []

            for rds_sg_id in rds_sg_ids:
                rds_sg = sg_map.get(rds_sg_id)
                if rds_sg:
                    matched = _rds_sg_permits_ec2_sg(rds_sg, ec2_sg_ids)
                    matched_sgs.extend(matched)

            if matched_sgs:
                relationships.append({
                    "RelationshipType": "CONNECTS_TO_RDS",
                    "TargetId": rds["ResourceId"],
                    "TargetType": "RDS",
                    "Attributes": {
                        "DBInstanceIdentifier": rds["ResourceId"],
                        "MatchedSecurityGroups": list(set(matched_sgs)),
                    },
                })

        # Only update if there are relationships to write
        if not relationships:
            continue

        try:
            table.update_item(
                Key={
                    "ResourceId": ec2["ResourceId"],
                    "ResourceType": ec2["ResourceType"],
                },
                UpdateExpression="SET Relationships = list_append(if_not_exists(Relationships, :empty), :rels)",
                ExpressionAttributeValues={
                    ":rels": relationships,
                    ":empty": [],
                },
            )
            updated += 1
            logger.info(
                "EC2→RDS relationships updated",
                extra={
                    "account_id": ec2.get("AccountId"),
                    "region": ec2.get("Region"),
                    "resource_id": ec2["ResourceId"],
                    "relationship_count": len(relationships),
                },
            )
        except ClientError as exc:
            logger.error(
                "Failed to update EC2→RDS relationships",
                extra={
                    "account_id": ec2.get("AccountId"),
                    "region": ec2.get("Region"),
                    "resource_id": ec2.get("ResourceId"),
                    "error": str(exc),
                },
            )

    logger.info("EC2→RDS enrichment complete", extra={"updated": updated})
    return updated
