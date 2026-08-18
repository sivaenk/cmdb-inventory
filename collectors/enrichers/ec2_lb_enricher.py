"""
EC2→LB Relationship Enricher.

Reads LB Resource Nodes (ALB, NLB, CLB) from DynamoDB, calls the
appropriate ELB APIs to identify registered EC2 instance targets, and
writes EC2→LB relationship entries to the matching EC2 nodes.
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


def _get_elbv2_instance_targets(session: boto3.Session, region: str, lb_arn: str) -> list[dict[str, Any]]:
    """
    For an ALB or NLB, return a list of registered EC2 instance targets with health status.
    Each entry: {instance_id, target_group_arn, health_status}
    """
    elbv2 = session.client("elbv2", region_name=region)
    targets: list[dict[str, Any]] = []

    try:
        tg_response = elbv2.describe_target_groups(LoadBalancerArn=lb_arn)
        for tg in tg_response.get("TargetGroups", []):
            tg_arn = tg["TargetGroupArn"]
            target_type = tg.get("TargetType", "")
            if target_type != "instance":
                continue  # only care about EC2 instance targets

            try:
                health_response = elbv2.describe_target_health(TargetGroupArn=tg_arn)
                for thd in health_response.get("TargetHealthDescriptions", []):
                    instance_id = thd.get("Target", {}).get("Id")
                    health_state = thd.get("TargetHealth", {}).get("State", "unknown")
                    if instance_id and instance_id.startswith("i-"):
                        targets.append({
                            "instance_id": instance_id,
                            "target_group_arn": tg_arn,
                            "health_status": health_state,
                        })
            except ClientError as exc:
                logger.warning(
                    "Failed to describe target health",
                    extra={"lb_arn": lb_arn, "tg_arn": tg_arn, "error": str(exc)},
                )
    except ClientError as exc:
        logger.warning(
            "Failed to describe target groups",
            extra={"lb_arn": lb_arn, "error": str(exc)},
        )

    return targets


def _get_clb_instance_targets(session: boto3.Session, region: str, lb_name: str) -> list[dict[str, Any]]:
    """
    For a CLB, return registered EC2 instance targets with health status.
    Each entry: {instance_id, health_status}
    """
    elb = session.client("elb", region_name=region)
    targets: list[dict[str, Any]] = []

    try:
        response = elb.describe_instance_health(LoadBalancerName=lb_name)
        for state in response.get("InstanceStates", []):
            instance_id = state.get("InstanceId")
            health_state = state.get("State", "unknown")
            if instance_id:
                targets.append({
                    "instance_id": instance_id,
                    "health_status": health_state,
                })
    except ClientError as exc:
        logger.warning(
            "Failed to describe CLB instance health",
            extra={"lb_name": lb_name, "error": str(exc)},
        )

    return targets


def enrich(table, session: boto3.Session, account_id: str | None = None, region: str | None = None) -> int:
    """
    Enrich EC2 nodes with EC2→LB relationships.

    Reads ALB, NLB, and CLB nodes from DynamoDB, calls ELB APIs to find
    registered EC2 targets, and appends EC2→LB relationship entries to
    the matching EC2 nodes.

    Parameters
    ----------
    table : DynamoDB Table resource
    session : boto3.Session for calling ELB APIs
    account_id : optional filter
    region : optional filter

    Returns the number of EC2 nodes updated.
    """
    # Collect all LB nodes (ALB, NLB, CLB)
    lb_nodes: list[dict[str, Any]] = []
    for lb_type in ("ALB", "NLB", "CLB"):
        lb_nodes.extend(_query_nodes_by_type(table, lb_type))

    if account_id:
        lb_nodes = [n for n in lb_nodes if n.get("AccountId") == account_id]
    if region:
        lb_nodes = [n for n in lb_nodes if n.get("Region") == region]

    if not lb_nodes:
        logger.info("No LB nodes to enrich", extra={"account_id": account_id, "region": region})
        return 0

    # Build a map: instance_id → list of relationship entries
    instance_relationships: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for lb in lb_nodes:
        lb_type = lb.get("ResourceType")
        lb_region = lb.get("Region", region or "us-east-1")
        lb_id = lb["ResourceId"]  # ARN for ALB/NLB, name for CLB

        if lb_type in ("ALB", "NLB"):
            targets = _get_elbv2_instance_targets(session, lb_region, lb_id)
            for t in targets:
                instance_relationships[t["instance_id"]].append({
                    "RelationshipType": "TARGET_OF_LB",
                    "TargetId": lb_id,
                    "TargetType": lb_type,
                    "Attributes": {
                        "LoadBalancerArn": lb_id,
                        "TargetGroupArn": t["target_group_arn"],
                        "HealthStatus": t["health_status"],
                    },
                })

        elif lb_type == "CLB":
            targets = _get_clb_instance_targets(session, lb_region, lb_id)
            for t in targets:
                instance_relationships[t["instance_id"]].append({
                    "RelationshipType": "TARGET_OF_LB",
                    "TargetId": lb_id,
                    "TargetType": "CLB",
                    "Attributes": {
                        "LoadBalancerName": lb_id,
                        "TargetGroupArn": None,
                        "HealthStatus": t["health_status"],
                    },
                })

    if not instance_relationships:
        logger.info("No EC2 instances found as LB targets", extra={"account_id": account_id, "region": region})
        return 0

    # Write relationships to each EC2 node
    updated = 0
    for instance_id, relationships in instance_relationships.items():
        try:
            table.update_item(
                Key={
                    "ResourceId": instance_id,
                    "ResourceType": "EC2",
                },
                UpdateExpression="SET Relationships = list_append(if_not_exists(Relationships, :empty), :rels)",
                ExpressionAttributeValues={
                    ":rels": relationships,
                    ":empty": [],
                },
            )
            updated += 1
            logger.info(
                "EC2→LB relationships updated",
                extra={
                    "resource_id": instance_id,
                    "relationship_count": len(relationships),
                },
            )
        except ClientError as exc:
            logger.error(
                "Failed to update EC2→LB relationships",
                extra={
                    "resource_id": instance_id,
                    "error": str(exc),
                },
            )

    logger.info("EC2→LB enrichment complete", extra={"updated": updated})
    return updated
