"""
Validation — Inventory counts.

Queries DynamoDB for resource counts per account/region/type and compares
them against the scan metadata records written by the orchestrator.
"""
import argparse
import json
import sys
from collections import defaultdict
from typing import Any

import boto3
from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError


def _query_all(table, **kwargs) -> list[dict]:
    """Paginate through a DynamoDB query/scan and return all items."""
    items: list[dict] = []
    last_key = None
    while True:
        if last_key:
            kwargs["ExclusiveStartKey"] = last_key
        response = table.query(**kwargs) if "KeyConditionExpression" in kwargs else table.scan(**kwargs)
        items.extend(response.get("Items", []))
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break
    return items


def _get_scan_metadata_records(table) -> list[dict]:
    """Return all SCAN_METADATA records from the table."""
    return _query_all(
        table,
        IndexName="TypeIndex",
        KeyConditionExpression=Key("ResourceType").eq("SCAN_METADATA"),
    )


def _get_actual_counts(table, account_id: str, region: str) -> dict[str, int]:
    """
    Count actual resource nodes per ResourceType for a given account/region,
    excluding SCAN_METADATA records.
    """
    items = _query_all(
        table,
        IndexName="AccountIndex",
        KeyConditionExpression=Key("AccountId").eq(account_id) & Key("Region").eq(region),
        FilterExpression=Attr("ResourceType").ne("SCAN_METADATA"),
    )
    counts: dict[str, int] = defaultdict(int)
    for item in items:
        counts[item["ResourceType"]] += 1

    # S3 buckets are stored under their actual region, not the scan region.
    all_account_items = _query_all(
        table,
        IndexName="AccountIndex",
        KeyConditionExpression=Key("AccountId").eq(account_id),
        FilterExpression=Attr("ResourceType").eq("S3"),
    )
    if all_account_items:
        counts["S3"] = len(all_account_items)

    return dict(counts)


def validate(table_name: str, region: str, session: boto3.Session | None = None) -> dict[str, Any]:
    """
    Run inventory validation and return a structured report dict.
    """
    boto_session = session or boto3.Session()
    dynamodb = boto_session.resource("dynamodb", region_name=region)
    table = dynamodb.Table(table_name)

    scan_records = _get_scan_metadata_records(table)

    report: dict[str, Any] = {"status": "PASS", "accounts": {}}

    if not scan_records:
        report["status"] = "FAIL"
        report["message"] = "No SCAN_METADATA records found in table."
        return report

    for record in scan_records:
        account_id = record.get("AccountId", "unknown")
        rec_region = record.get("Region", "unknown")
        key = f"{account_id}#{rec_region}"

        expected_counts: dict[str, int] = record.get("ResourceCounts", {})
        actual_counts = _get_actual_counts(table, account_id, rec_region)

        type_results: dict[str, Any] = {}
        account_pass = True

        for rtype, expected in expected_counts.items():
            actual = actual_counts.get(rtype, 0)
            match = actual == expected
            if not match:
                account_pass = False
            type_results[rtype] = {
                "expected": int(expected),
                "actual": actual,
                "match": match,
            }

        for rtype, actual in actual_counts.items():
            if rtype not in type_results:
                type_results[rtype] = {
                    "expected": None,
                    "actual": actual,
                    "match": False,
                    "note": "present in table but not in scan metadata",
                }
                account_pass = False

        if not account_pass:
            report["status"] = "FAIL"

        report["accounts"][key] = {
            "scan_metadata_found": True,
            "scan_start_time": record.get("ScanStartTime"),
            "scan_end_time": record.get("ScanEndTime"),
            "resource_type_results": type_results,
            "overall": "PASS" if account_pass else "FAIL",
        }

    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate CMDB inventory counts against scan metadata.")
    parser.add_argument("--table-name", required=True, help="DynamoDB table name")
    parser.add_argument("--region", required=True, help="AWS region of the DynamoDB table")
    args = parser.parse_args()

    try:
        report = validate(table_name=args.table_name, region=args.region)
    except ClientError as exc:
        sys.exit(f"AWS error: {exc}")

    print(json.dumps(report, indent=2))
    if report.get("status") != "PASS":
        sys.exit(1)


if __name__ == "__main__":
    main()
