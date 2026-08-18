"""
S3 Bucket collector.
"""
import boto3
from botocore.exceptions import ClientError

from aws_resource_collectors.models.resource_node import ResourceNode
from aws_resource_collectors.utils.logger import get_logger

logger = get_logger(__name__)

_UNAVAILABLE = "unavailable"


def _get_bucket_region(s3_client, bucket_name: str) -> str:
    """Return the bucket's region, or 'unavailable' on access error."""
    try:
        resp = s3_client.get_bucket_location(Bucket=bucket_name)
        # us-east-1 returns None from get_bucket_location
        return resp.get("LocationConstraint") or "us-east-1"
    except ClientError as exc:
        logger.warning(
            "Cannot get bucket location",
            extra={"bucket": bucket_name, "error": str(exc)},
        )
        return _UNAVAILABLE


def _get_versioning(s3_client, bucket_name: str) -> str:
    """Return versioning state string, or 'unavailable' on access error."""
    try:
        resp = s3_client.get_bucket_versioning(Bucket=bucket_name)
        return resp.get("Status", "Disabled")
    except ClientError as exc:
        logger.warning(
            "Cannot get bucket versioning",
            extra={"bucket": bucket_name, "error": str(exc)},
        )
        return _UNAVAILABLE


def _get_bucket_tags(s3_client, bucket_name: str) -> dict:
    """Return bucket tags as {key: value}, or empty dict on access error."""
    try:
        resp = s3_client.get_bucket_tagging(Bucket=bucket_name)
        return {t["Key"]: t["Value"] for t in resp.get("TagSet", [])}
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        if code == "NoSuchTagSet":
            return {}
        logger.warning(
            "Cannot get bucket tags",
            extra={"bucket": bucket_name, "error": str(exc)},
        )
        return {}


def _get_public_access_block(s3_client, bucket_name: str) -> dict | str:
    """Return PublicAccessBlock config dict, or 'unavailable' on access error."""
    try:
        resp = s3_client.get_public_access_block(Bucket=bucket_name)
        return resp.get("PublicAccessBlockConfiguration", {})
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        if code == "NoSuchPublicAccessBlockConfiguration":
            return {}
        logger.warning(
            "Cannot get public access block",
            extra={"bucket": bucket_name, "error": str(exc)},
        )
        return _UNAVAILABLE


def collect(session: boto3.Session, account_id: str, region: str) -> list[ResourceNode]:
    """
    Collect all S3 buckets visible to this account.
    S3 is a global service; buckets are listed once and filtered/enriched per bucket.
    Gracefully handles per-bucket access denied.
    """
    s3 = session.client("s3", region_name="us-east-1")
    nodes: list[ResourceNode] = []

    try:
        resp = s3.list_buckets()
        buckets = resp.get("Buckets", [])
    except ClientError as exc:
        logger.error(
            "S3 ListBuckets failed",
            extra={"account_id": account_id, "error": str(exc)},
        )
        return nodes

    for bucket in buckets:
        name = bucket["Name"]
        creation_date = bucket.get("CreationDate", "")
        if hasattr(creation_date, "isoformat"):
            creation_date = creation_date.isoformat()

        bucket_region = _get_bucket_region(s3, name)
        versioning = _get_versioning(s3, name)
        public_access_block = _get_public_access_block(s3, name)
        tags = _get_bucket_tags(s3, name)

        metadata = {
            "BucketName": name,
            "Region": bucket_region,
            "CreationDate": creation_date,
            "Versioning": versioning,
            "PublicAccessBlock": public_access_block,
            "Tags": tags,
        }

        nodes.append(
            ResourceNode(
                ResourceId=name,
                ResourceType="S3",
                AccountId=account_id,
                Region=bucket_region if bucket_region != _UNAVAILABLE else region,
                Metadata=metadata,
                Relationships=[],
            )
        )

    logger.info(
        "S3 collection complete",
        extra={"account_id": account_id, "region": region, "count": len(nodes)},
    )
    return nodes
