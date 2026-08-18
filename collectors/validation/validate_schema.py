"""
Validation — Schema completeness.

For each resource type, verifies that all required metadata fields are
present and non-null.
"""
import argparse
import json
import sys
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

# Required metadata fields per resource type
REQUIRED_FIELDS: dict[str, list[str]] = {
    "EC2": [
        "InstanceType", "State", "AmiId", "VpcId", "SubnetId",
        "AvailabilityZone", "PrivateIp", "LaunchTime", "SecurityGroupIds", "Tags",
    ],
    "EBS": [
        "VolumeType", "SizeGiB", "State", "Encrypted",
        "AvailabilityZone", "AttachmentState", "Tags",
    ],
    "RDS": [
        "DBInstanceClass", "Engine", "EngineVersion", "DBInstanceStatus",
        "Endpoint", "VpcId", "MultiAZ", "StorageType", "AllocatedStorage", "Tags",
    ],
    "ALB": ["LoadBalancerArn", "DNSName", "Type", "Scheme", "State", "Tags"],
    "NLB": ["LoadBalancerArn", "DNSName", "Type", "Scheme", "State", "Tags"],
    "CLB": ["LoadBalancerName", "DNSName", "Type", "Scheme", "State", "Tags"],
    "EFS": ["LifeCycleState", "PerformanceMode", "ThroughputMode", "SizeInBytes", "Encrypted", "Tags"],
    "S3": ["BucketName", "Region", "Tags"],
    "VPC": ["CidrBlock", "State", "IsDefault", "Tags"],
    "EIP": ["PublicIp", "Domain", "Tags"],
    "TGW": ["State", "OwnerId", "Tags"],
    "RouteTable": ["VpcId", "AssociatedSubnetIds", "Routes", "Tags"],
}

REQUIRED_TOP_LEVEL = ["ResourceId", "ResourceType", "AccountId", "Region", "DiscoveredAt", "LastSeenAt"]


def _query_by_type(table, resource_type: str) -> list[dict]:
    """Return all records of a given ResourceType using the TypeIndex GSI."""
    items: list[dict] = []
    last_key = None
    while True:
        kwargs: dict[str, Any] = {
            "IndexName": "TypeIndex",
            "KeyConditionExpression": Key("ResourceType").eq(resource_type),
        }
        if last_key:
            kwargs["ExclusiveStartKey"] = last_key
        response = table.query(**kwargs)
        items.extend(response.get("Items", []))
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break
    return items


def _check_record(record: dict, required_metadata_fields: list[str]) -> list[str]:
    """Return a list of missing/null field paths for a single record."""
    missing: list[str] = []

    for field in REQUIRED_TOP_LEVEL:
        if not record.get(field):
            missing.append(field)

    metadata = record.get("Metadata", {})
    if not isinstance(metadata, dict):
        missing.append("Metadata (not a dict)")
        return missing

    for field in required_metadata_fields:
        value = metadata.get(field)
        if value is None or value == "":
            missing.append(f"Metadata.{field}")

    return missing


def validate(
    table_name: str,
    region: str,
    resource_types: list[str] | None = None,
    session: boto3.Session | None = None,
) -> dict[str, Any]:
    """Run schema validation and return a structured report dict."""
    boto_session = session or boto3.Session()
    dynamodb = boto_session.resource("dynamodb", region_name=region)
    table = dynamodb.Table(table_name)

    types_to_check = resource_types or list(REQUIRED_FIELDS.keys())
    report: dict[str, Any] = {"status": "PASS", "resource_types": {}}

    for rtype in types_to_check:
        required_meta = REQUIRED_FIELDS.get(rtype, [])
        records = _query_by_type(table, rtype)

        violations: list[dict] = []
        for record in records:
            missing = _check_record(record, required_meta)
            if missing:
                violations.append({
                    "ResourceId": record.get("ResourceId", "<unknown>"),
                    "AccountId": record.get("AccountId", "<unknown>"),
                    "Region": record.get("Region", "<unknown>"),
                    "missing_fields": missing,
                })

        if violations:
            report["status"] = "FAIL"

        report["resource_types"][rtype] = {
            "total_records": len(records),
            "records_with_missing_fields": len(violations),
            "violations": violations,
        }

    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate CMDB resource schema completeness.")
    parser.add_argument("--table-name", required=True, help="DynamoDB table name")
    parser.add_argument("--region", required=True, help="AWS region of the DynamoDB table")
    parser.add_argument("--resource-types", nargs="+", choices=list(REQUIRED_FIELDS.keys()))
    args = parser.parse_args()

    try:
        report = validate(table_name=args.table_name, region=args.region, resource_types=args.resource_types)
    except ClientError as exc:
        sys.exit(f"AWS error: {exc}")

    print(json.dumps(report, indent=2))
    if report.get("status") != "PASS":
        sys.exit(1)


if __name__ == "__main__":
    main()
