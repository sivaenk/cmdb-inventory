"""Storage layer for CMDB Backend."""

from store.dynamodb_store import DynamoDBStore
from store.s3_store import S3Store

__all__ = ["DynamoDBStore", "S3Store"]
