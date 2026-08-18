"""
S3 store layer for the CMDB.

Writes ResourceNode lists as JSON to a partitioned S3 path:
  s3://<bucket>/inventory/account_id=<id>/region=<r>/resource_type=<t>/scan_date=<d>/data.json

This layout supports Athena-based analytical queries.
"""
import json
from typing import Any

import boto3
from botocore.exceptions import ClientError

from aws_resource_collectors.models import ResourceNode
from aws_resource_collectors.utils import get_logger

logger = get_logger(__name__)


class S3Store:
    """Wraps S3 write operations for the CMDB analytical store."""

    def __init__(self, bucket_name: str, session: boto3.Session | None = None) -> None:
        self.bucket_name = bucket_name
        boto_session = session or boto3.Session()
        self.s3_client = boto_session.client("s3")

    def write_partition(
        self,
        nodes: list[ResourceNode],
        account_id: str,
        region: str,
        resource_type: str,
        scan_date: str,
    ) -> str:
        """
        Serialize nodes to JSON and write to the partitioned S3 path.

        The S3 key follows the Hive-style partition layout expected by Athena:
          inventory/account_id=<account_id>/region=<region>/
            resource_type=<resource_type>/scan_date=<scan_date>/data.json

        Returns the full S3 key that was written.
        """
        key = (
            f"inventory/"
            f"account_id={account_id}/"
            f"region={region}/"
            f"resource_type={resource_type}/"
            f"scan_date={scan_date}/"
            f"data.json"
        )

        payload: list[dict[str, Any]] = [n.to_dict() for n in nodes]
        body = json.dumps(payload, default=str).encode("utf-8")

        try:
            self.s3_client.put_object(
                Bucket=self.bucket_name,
                Key=key,
                Body=body,
                ContentType="application/json",
            )
            logger.info(
                "Wrote S3 partition",
                extra={
                    "account_id": account_id,
                    "region": region,
                    "resource_type": resource_type,
                    "scan_date": scan_date,
                    "record_count": len(nodes),
                    "s3_key": key,
                },
            )
        except ClientError as exc:
            logger.error(
                "Failed to write S3 partition",
                extra={
                    "account_id": account_id,
                    "region": region,
                    "resource_type": resource_type,
                    "error": str(exc),
                    "s3_key": key,
                },
            )
            raise

        return key
