"""
CMDB Backend Orchestrator — AWS resource inventory with DynamoDB + S3 persistence.

Entry point for full inventory scans. Responsibilities:
  1. List all active accounts via organizations:ListAccounts
  2. For each account × region, assume cross-account role
  3. Verify assumed identity via sts:GetCallerIdentity
  4. Run all collectors
  5. Write ResourceNodes to DynamoDB + S3
  6. Write scan metadata record per account/region
  7. Optionally trigger enrichment (--run-enrichment flag)
"""
import argparse
import os
from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.exceptions import ClientError

from aws_resource_collectors.collectors import (
    ebs_collector,
    ec2_collector,
    efs_collector,
    eip_collector,
    lb_collector,
    rds_collector,
    route_table_collector,
    s3_collector,
    tgw_collector,
    vpc_collector,
)
from aws_resource_collectors.models import ResourceNode
from aws_resource_collectors.utils import assume_role, get_local_account_id, verify_identity, get_logger

from store.dynamodb_store import DynamoDBStore
from store.s3_store import S3Store

logger = get_logger(__name__)

# Collectors that run per-region
_REGIONAL_COLLECTORS = [
    ("EC2", ec2_collector),
    ("EBS", ebs_collector),
    ("RDS", rds_collector),
    ("LB", lb_collector),
    ("EFS", efs_collector),
    ("VPC", vpc_collector),
    ("EIP", eip_collector),
    ("TGW", tgw_collector),
    ("RouteTable", route_table_collector),
]

# S3 is global — run once per account
_GLOBAL_COLLECTORS = [
    ("S3", s3_collector),
]


def _list_active_accounts(org_client) -> list[dict]:
    """Return all ACTIVE accounts in the Organization."""
    accounts: list[dict] = []
    paginator = org_client.get_paginator("list_accounts")
    for page in paginator.paginate():
        for acct in page.get("Accounts", []):
            if acct.get("Status") == "ACTIVE":
                accounts.append(acct)
    return accounts


def _run_collectors_for_region(
    session: boto3.Session,
    account_id: str,
    region: str,
) -> tuple[dict[str, list[ResourceNode]], dict[str, int]]:
    """Run all regional collectors for a single account/region pair."""
    nodes_by_type: dict[str, list[ResourceNode]] = {}
    counts: dict[str, int] = {}

    for resource_type, collector_module in _REGIONAL_COLLECTORS:
        try:
            nodes = collector_module.collect(session, account_id, region)
            nodes_by_type[resource_type] = nodes
            counts[resource_type] = len(nodes)
        except Exception as exc:
            logger.error(
                "Collector failed",
                extra={"account_id": account_id, "region": region, "resource_type": resource_type, "error": str(exc)},
            )
            nodes_by_type[resource_type] = []
            counts[resource_type] = 0

    return nodes_by_type, counts


def _run_global_collectors(
    session: boto3.Session,
    account_id: str,
    region: str,
) -> tuple[dict[str, list[ResourceNode]], dict[str, int]]:
    """Run global collectors once per account."""
    nodes_by_type: dict[str, list[ResourceNode]] = {}
    counts: dict[str, int] = {}

    for resource_type, collector_module in _GLOBAL_COLLECTORS:
        try:
            nodes = collector_module.collect(session, account_id, region)
            nodes_by_type[resource_type] = nodes
            counts[resource_type] = len(nodes)
        except Exception as exc:
            logger.error(
                "Global collector failed",
                extra={"account_id": account_id, "resource_type": resource_type, "error": str(exc)},
            )
            nodes_by_type[resource_type] = []
            counts[resource_type] = 0

    return nodes_by_type, counts


def _persist_nodes(
    ddb_store: DynamoDBStore,
    s3_store: S3Store | None,
    nodes_by_type: dict[str, list[ResourceNode]],
    account_id: str,
    region: str,
    scan_date: str,
) -> None:
    """Write all nodes to DynamoDB (primary) and S3 (analytical)."""
    for resource_type, nodes in nodes_by_type.items():
        # DynamoDB — upsert each node
        for node in nodes:
            try:
                ddb_store.upsert_resource(node)
            except Exception as exc:
                logger.error(
                    "DynamoDB upsert failed",
                    extra={"account_id": account_id, "region": region, "resource_type": resource_type, "error": str(exc)},
                )

        # S3 — write partition if configured
        if s3_store:
            try:
                s3_store.write_partition(nodes, account_id, region, resource_type, scan_date)
            except Exception as exc:
                logger.error(
                    "S3 write failed",
                    extra={"account_id": account_id, "region": region, "resource_type": resource_type, "error": str(exc)},
                )


def _write_scan_metadata(
    ddb_store: DynamoDBStore,
    account_id: str,
    region: str,
    scan_start: str,
    scan_end: str,
    resource_counts: dict[str, int],
) -> None:
    """Persist a scan summary record to DynamoDB."""
    record: dict[str, Any] = {
        "ResourceId": f"SCAN#{account_id}#{region}#{scan_start}",
        "ResourceType": "SCAN_METADATA",
        "AccountId": account_id,
        "Region": region,
        "ScanStartTime": scan_start,
        "ScanEndTime": scan_end,
        "ResourceCounts": resource_counts,
    }
    try:
        ddb_store.write_scan_metadata(record)
    except Exception as exc:
        logger.error("Failed to write scan metadata", extra={"account_id": account_id, "region": region, "error": str(exc)})


def _filter_accounts(accounts: list[dict], target_account_ids: list[str] | None) -> list[dict]:
    """Filter accounts to only those in target list. If target list is empty/None, return all."""
    if not target_account_ids:
        return accounts
    target_set = set(target_account_ids)
    return [acct for acct in accounts if acct["Id"] in target_set]


def run(
    regions: list[str],
    role_name: str,
    dynamodb_table: str,
    s3_bucket: str,
    dynamodb_region: str,
    external_id: str | None = None,
    run_enrichment: bool = False,
    target_account_ids: list[str] | None = None,
) -> None:
    """Main orchestration loop."""
    # Initialize stores
    ddb_store = DynamoDBStore(table_name=dynamodb_table, region=dynamodb_region)
    s3_store = S3Store(bucket_name=s3_bucket) if s3_bucket else None

    # Detect local account to skip self-assumption
    local_account_id = get_local_account_id()
    logger.info("Local account detected", extra={"local_account_id": local_account_id})

    # List all active member accounts
    org_client = boto3.client("organizations")
    try:
        all_accounts = _list_active_accounts(org_client)
    except ClientError as exc:
        logger.error("Failed to list Organization accounts", extra={"error": str(exc)})
        raise

    # Filter to target accounts if specified
    accounts = _filter_accounts(all_accounts, target_account_ids)
    
    if target_account_ids:
        logger.info(
            "Account filtering enabled",
            extra={"total_org_accounts": len(all_accounts), "target_accounts": len(accounts), "target_ids": target_account_ids},
        )
    else:
        logger.info("Scanning all accounts", extra={"account_count": len(accounts)})

    logger.info("Starting inventory scan", extra={"account_count": len(accounts), "regions": regions})

    for account in accounts:
        account_id = account["Id"]
        account_name = account.get("Name", "")

        logger.info("Processing account", extra={"account_id": account_id, "account_name": account_name})

        # Use ambient credentials for local account
        if account_id == local_account_id:
            session = boto3.Session()
            logger.info("Using ambient credentials for local account", extra={"account_id": account_id})
            verified_account_id = account_id
        else:
            session = assume_role(account_id, role_name, external_id=external_id)
            if session is None:
                logger.error("Skipping account — role assumption failed", extra={"account_id": account_id})
                continue

            verified_account_id = verify_identity(session)
            if verified_account_id is None:
                logger.error("Skipping account — identity verification failed", extra={"account_id": account_id})
                continue

        # Run global collectors once per account
        primary_region = regions[0]
        global_nodes_by_type, global_counts = _run_global_collectors(session, verified_account_id, primary_region)

        for region in regions:
            scan_start = datetime.now(timezone.utc).isoformat()
            scan_date = scan_start[:10]

            logger.info("Scanning region", extra={"account_id": verified_account_id, "region": region})

            # Run regional collectors
            regional_nodes_by_type, regional_counts = _run_collectors_for_region(session, verified_account_id, region)

            # Merge global nodes into first region only
            all_nodes_by_type = dict(regional_nodes_by_type)
            all_counts = dict(regional_counts)
            if region == primary_region:
                all_nodes_by_type.update(global_nodes_by_type)
                all_counts.update(global_counts)

            # Persist to DynamoDB + S3
            _persist_nodes(ddb_store, s3_store, all_nodes_by_type, verified_account_id, region, scan_date)

            scan_end = datetime.now(timezone.utc).isoformat()

            # Write scan metadata
            _write_scan_metadata(ddb_store, verified_account_id, region, scan_start, scan_end, all_counts)

            logger.info("Region scan complete", extra={"account_id": verified_account_id, "region": region, "counts": all_counts})

    logger.info("Inventory scan complete")

    if run_enrichment:
        _trigger_enrichment(dynamodb_table, dynamodb_region)


def _trigger_enrichment(dynamodb_table: str, dynamodb_region: str) -> None:
    """Invoke enrichers — EC2→EBS, EC2→RDS, EC2→LB."""
    from aws_resource_collectors.enrichers import ec2_ebs_enricher, ec2_rds_enricher, ec2_lb_enricher

    dynamodb = boto3.resource("dynamodb", region_name=dynamodb_region)
    table = dynamodb.Table(dynamodb_table)
    session = boto3.Session()

    logger.info("Enrichment starting")

    updated_ebs = ec2_ebs_enricher.enrich(table)
    logger.info("EC2→EBS enrichment done", extra={"updated": updated_ebs})

    updated_rds = ec2_rds_enricher.enrich(table, session)
    logger.info("EC2→RDS enrichment done", extra={"updated": updated_rds})

    updated_lb = ec2_lb_enricher.enrich(table, session)
    logger.info("EC2→LB enrichment done", extra={"updated": updated_lb})

    logger.info("Enrichment complete", extra={"ebs": updated_ebs, "rds": updated_rds, "lb": updated_lb})


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="CMDB Backend — Inventory Orchestrator")
    parser.add_argument("--regions", nargs="+", default=os.environ.get("CMDB_REGIONS", "us-east-1").split(","))
    parser.add_argument("--role-name", default=os.environ.get("CMDB_ROLE_NAME", "CMDBInventoryRole"))
    parser.add_argument("--dynamodb-table", default=os.environ.get("CMDB_DYNAMODB_TABLE", "CMDBInventory"))
    parser.add_argument("--s3-bucket", default=os.environ.get("CMDB_S3_BUCKET", ""))
    parser.add_argument("--dynamodb-region", default=os.environ.get("CMDB_DYNAMODB_REGION", "us-east-1"))
    parser.add_argument("--external-id", default=os.environ.get("CMDB_EXTERNAL_ID", ""))
    parser.add_argument("--run-enrichment", action="store_true", default=False)
    parser.add_argument("--target-accounts", nargs="*", default=os.environ.get("TARGET_ACCOUNT_IDS", "").split(",") if os.environ.get("TARGET_ACCOUNT_IDS") else [])
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    # Filter empty strings from target accounts
    target_accounts = [a.strip() for a in args.target_accounts if a.strip()]
    run(
        regions=args.regions,
        role_name=args.role_name,
        dynamodb_table=args.dynamodb_table,
        s3_bucket=args.s3_bucket,
        dynamodb_region=args.dynamodb_region,
        external_id=args.external_id or None,
        run_enrichment=args.run_enrichment,
        target_account_ids=target_accounts or None,
    )


def lambda_handler(event: dict, context: Any) -> dict:
    """AWS Lambda entry point."""
    regions_raw = os.environ.get("SCAN_REGIONS", "us-east-1")
    regions = [r.strip() for r in regions_raw.split(",") if r.strip()]
    role_name = os.environ.get("CROSS_ACCOUNT_ROLE_NAME", "CMDBInventoryRole")
    dynamodb_table = os.environ.get("DYNAMODB_TABLE_NAME", "CMDBInventory")
    s3_bucket = os.environ.get("S3_BUCKET_NAME", "")
    dynamodb_region = os.environ.get("AWS_REGION", "us-east-1")
    external_id = os.environ.get("CMDB_EXTERNAL_ID", "") or None
    run_enrichment_flag = os.environ.get("RUN_ENRICHMENT", "false").lower() == "true"
    
    # Parse target accounts — empty string means all accounts
    target_accounts_raw = os.environ.get("TARGET_ACCOUNT_IDS", "")
    target_account_ids = [a.strip() for a in target_accounts_raw.split(",") if a.strip()] or None

    logger.info("Lambda invoked", extra={
        "source": event.get("source", "unknown"),
        "regions": regions,
        "target_accounts": target_account_ids or "ALL",
    })

    run(
        regions=regions,
        role_name=role_name,
        dynamodb_table=dynamodb_table,
        s3_bucket=s3_bucket,
        dynamodb_region=dynamodb_region,
        external_id=external_id,
        run_enrichment=run_enrichment_flag,
        target_account_ids=target_account_ids,
    )

    return {"status": "ok", "regions": regions, "target_accounts": target_account_ids or "ALL"}
