"""
Validation — Relationship enrichment.

Queries a sample of EC2 nodes from DynamoDB and reports counts of
EC2→EBS, EC2→RDS, and EC2→LB relationships found after enrichment.
"""
import argparse
import json
import sys
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

_REL_EBS = "ATTACHED_EBS"
_REL_RDS = "CONNECTS_TO_RDS"
_REL_LB_TYPES = {"TARGET_OF_LB", "TARGET_OF_ALB", "TARGET_OF_NLB", "TARGET_OF_CLB"}
_DEFAULT_SAMPLE_SIZE = 100


def _scan_ec2_nodes(table, limit: int) -> list[dict]:
    """Return up to `limit` EC2 ResourceNodes from the TypeIndex GSI."""
    items: list[dict] = []
    last_key = None

    while len(items) < limit:
        kwargs: dict[str, Any] = {
            "IndexName": "TypeIndex",
            "KeyConditionExpression": Key("ResourceType").eq("EC2"),
            "Limit": min(limit - len(items), 100),
        }
        if last_key:
            kwargs["ExclusiveStartKey"] = last_key

        response = table.query(**kwargs)
        items.extend(response.get("Items", []))
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break

    return items[:limit]


def _count_relationships(nodes: list[dict]) -> dict[str, Any]:
    """Iterate over EC2 nodes and count relationship types."""
    total = len(nodes)
    nodes_with_any = 0
    nodes_with_ebs = 0
    nodes_with_rds = 0
    nodes_with_lb = 0
    count_ebs = 0
    count_rds = 0
    count_lb = 0

    for node in nodes:
        rels: list[dict] = node.get("Relationships", [])
        if not isinstance(rels, list):
            rels = []

        ebs_rels = [r for r in rels if r.get("RelationshipType") == _REL_EBS]
        rds_rels = [r for r in rels if r.get("RelationshipType") == _REL_RDS]
        lb_rels = [r for r in rels if r.get("RelationshipType") in _REL_LB_TYPES]

        count_ebs += len(ebs_rels)
        count_rds += len(rds_rels)
        count_lb += len(lb_rels)

        if ebs_rels:
            nodes_with_ebs += 1
        if rds_rels:
            nodes_with_rds += 1
        if lb_rels:
            nodes_with_lb += 1
        if rels:
            nodes_with_any += 1

    return {
        "ec2_nodes_sampled": total,
        "ec2_nodes_with_any_relationship": nodes_with_any,
        "ec2_nodes_with_ebs": nodes_with_ebs,
        "ec2_nodes_with_rds": nodes_with_rds,
        "ec2_nodes_with_lb": nodes_with_lb,
        "ec2_to_ebs_count": count_ebs,
        "ec2_to_rds_count": count_rds,
        "ec2_to_lb_count": count_lb,
    }


def validate(
    table_name: str,
    region: str,
    sample_size: int = _DEFAULT_SAMPLE_SIZE,
    session: boto3.Session | None = None,
) -> dict[str, Any]:
    """Run relationship validation and return a structured report dict."""
    boto_session = session or boto3.Session()
    dynamodb = boto_session.resource("dynamodb", region_name=region)
    table = dynamodb.Table(table_name)

    nodes = _scan_ec2_nodes(table, limit=sample_size)

    if not nodes:
        return {
            "status": "NO_DATA",
            "sample_size_requested": sample_size,
            "counts": {},
            "message": "No EC2 nodes found in the CMDB table.",
        }

    counts = _count_relationships(nodes)

    missing_types = []
    if counts["ec2_to_ebs_count"] == 0:
        missing_types.append("EC2→EBS")
    if counts["ec2_to_rds_count"] == 0:
        missing_types.append("EC2→RDS")
    if counts["ec2_to_lb_count"] == 0:
        missing_types.append("EC2→LB")

    if not missing_types:
        status = "PASS"
        message = "All relationship types found in sample."
    else:
        status = "WARN"
        message = f"No relationships found for: {', '.join(missing_types)}."

    return {
        "status": status,
        "sample_size_requested": sample_size,
        "counts": counts,
        "message": message,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate EC2 relationship enrichment.")
    parser.add_argument("--table-name", required=True, help="DynamoDB table name")
    parser.add_argument("--region", required=True, help="AWS region of the DynamoDB table")
    parser.add_argument("--sample-size", type=int, default=_DEFAULT_SAMPLE_SIZE)
    args = parser.parse_args()

    try:
        report = validate(table_name=args.table_name, region=args.region, sample_size=args.sample_size)
    except ClientError as exc:
        sys.exit(f"AWS error: {exc}")

    print(json.dumps(report, indent=2))
    if report.get("status") == "NO_DATA":
        sys.exit(1)


if __name__ == "__main__":
    main()
