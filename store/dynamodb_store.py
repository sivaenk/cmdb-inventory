"""
DynamoDB store layer for the CMDB.

Table design:
  PK: ResourceId (String)
  SK: ResourceType (String)
  GSI AccountIndex: AccountId (PK) + Region (SK)
  GSI TypeIndex:    ResourceType (PK)
"""
import time
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from aws_resource_collectors.models import ResourceNode
from aws_resource_collectors.utils import get_logger

logger = get_logger(__name__)

_MAX_RETRIES = 3


def _retry_write(fn, *args, **kwargs) -> Any:
    """Retry a DynamoDB write up to _MAX_RETRIES times with exponential backoff."""
    for attempt in range(_MAX_RETRIES):
        try:
            return fn(*args, **kwargs)
        except ClientError as exc:
            if attempt == _MAX_RETRIES - 1:
                logger.error("DynamoDB write failed after retries", extra={"error": str(exc), "attempts": _MAX_RETRIES})
                raise
            wait = 2 ** attempt
            logger.warning("DynamoDB write failed, retrying", extra={"error": str(exc), "attempt": attempt + 1, "wait_seconds": wait})
            time.sleep(wait)


class DynamoDBStore:
    """Wraps DynamoDB operations for the CMDB table."""

    def __init__(self, table_name: str, region: str, session: boto3.Session | None = None) -> None:
        self.table_name = table_name
        boto_session = session or boto3.Session()
        dynamodb = boto_session.resource("dynamodb", region_name=region)
        self.table = dynamodb.Table(table_name)

    def upsert_resource(self, node: ResourceNode) -> None:
        """
        Write or update a ResourceNode in DynamoDB.

        Upsert semantics:
        - If the item does not exist, write it as-is (DiscoveredAt = now).
        - If the item already exists, preserve the original DiscoveredAt and
          update all other fields including LastSeenAt.
        """
        item = node.to_dict()

        def _write():
            try:
                self.table.put_item(
                    Item=item,
                    ConditionExpression="attribute_not_exists(ResourceId)",
                )
                logger.info("Inserted new resource", extra={"account_id": node.AccountId, "region": node.Region, "resource_type": node.ResourceType})
            except ClientError as exc:
                if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
                    raise
                # Item already exists — update everything except DiscoveredAt
                self.table.update_item(
                    Key={"ResourceId": node.ResourceId, "ResourceType": node.ResourceType},
                    UpdateExpression=(
                        "SET AccountId = :aid, #reg = :reg, Metadata = :meta, "
                        "Relationships = :rels, LastSeenAt = :lsa"
                    ),
                    ExpressionAttributeNames={"#reg": "Region"},
                    ExpressionAttributeValues={
                        ":aid": node.AccountId,
                        ":reg": node.Region,
                        ":meta": node.Metadata,
                        ":rels": node.Relationships,
                        ":lsa": node.LastSeenAt,
                    },
                )
                logger.info("Updated existing resource", extra={"account_id": node.AccountId, "region": node.Region, "resource_type": node.ResourceType})

        _retry_write(_write)

    def write_scan_metadata(self, record: dict) -> None:
        """Persist a scan summary record to DynamoDB."""
        def _write():
            self.table.put_item(Item=record)

        _retry_write(_write)
        logger.info("Wrote scan metadata", extra={"account_id": record.get("AccountId"), "region": record.get("Region")})

    def mark_stale_resources(
        self,
        account_id: str,
        region: str,
        scan_time: str,
        threshold_days: int = 2,
    ) -> int:
        """Mark ResourceNodes as stale when LastSeenAt is older than threshold_days."""
        from datetime import datetime, timedelta, timezone

        scan_dt = datetime.fromisoformat(scan_time.replace("Z", "+00:00"))
        cutoff_dt = scan_dt - timedelta(days=threshold_days)
        cutoff_iso = cutoff_dt.isoformat()

        stale_count = 0
        last_evaluated_key = None

        while True:
            query_kwargs: dict[str, Any] = {
                "IndexName": "AccountIndex",
                "KeyConditionExpression": Key("AccountId").eq(account_id) & Key("Region").eq(region),
                "FilterExpression": "LastSeenAt < :cutoff",
                "ExpressionAttributeValues": {":cutoff": cutoff_iso},
            }
            if last_evaluated_key:
                query_kwargs["ExclusiveStartKey"] = last_evaluated_key

            response = self.table.query(**query_kwargs)

            for item in response.get("Items", []):
                try:
                    self.table.update_item(
                        Key={"ResourceId": item["ResourceId"], "ResourceType": item["ResourceType"]},
                        UpdateExpression="SET #s = :stale",
                        ExpressionAttributeNames={"#s": "Status"},
                        ExpressionAttributeValues={":stale": "stale"},
                    )
                    stale_count += 1
                except ClientError as exc:
                    logger.error("Failed to mark resource stale", extra={"resource_id": item.get("ResourceId"), "error": str(exc)})

            last_evaluated_key = response.get("LastEvaluatedKey")
            if not last_evaluated_key:
                break

        logger.info("Marked stale resources", extra={"account_id": account_id, "region": region, "stale_count": stale_count})
        return stale_count
