"""
RDS Instance collector.
"""
import boto3
from botocore.exceptions import ClientError

from aws_resource_collectors.models.resource_node import ResourceNode
from aws_resource_collectors.utils.logger import get_logger

logger = get_logger(__name__)


def collect(session: boto3.Session, account_id: str, region: str) -> list[ResourceNode]:
    """
    Collect all RDS instances and return ResourceNode list.
    """
    rds = session.client("rds", region_name=region)
    nodes: list[ResourceNode] = []

    try:
        paginator = rds.get_paginator("describe_db_instances")

        for page in paginator.paginate():
            for db in page.get("DBInstances", []):
                db_id = db["DBInstanceIdentifier"]

                tags = {t["Key"]: t["Value"] for t in db.get("TagList", [])}
                sg_ids = [sg["VpcSecurityGroupId"] for sg in db.get("VpcSecurityGroups", [])]

                endpoint = db.get("Endpoint", {})
                subnet_group = db.get("DBSubnetGroup", {})

                metadata = {
                    "DBInstanceClass": db.get("DBInstanceClass"),
                    "Engine": db.get("Engine"),
                    "EngineVersion": db.get("EngineVersion"),
                    "DBInstanceStatus": db.get("DBInstanceStatus"),
                    "Endpoint": {
                        "Host": endpoint.get("Address"),
                        "Port": endpoint.get("Port"),
                    },
                    "VpcId": subnet_group.get("VpcId"),
                    "SubnetGroup": subnet_group.get("DBSubnetGroupName"),
                    "MultiAZ": db.get("MultiAZ"),
                    "StorageType": db.get("StorageType"),
                    "AllocatedStorage": db.get("AllocatedStorage"),
                    "VpcSecurityGroupIds": sg_ids,
                    "Tags": tags,
                }

                nodes.append(
                    ResourceNode(
                        ResourceId=db_id,
                        ResourceType="RDS",
                        AccountId=account_id,
                        Region=region,
                        Metadata=metadata,
                        Relationships=[],
                    )
                )

        logger.info(
            "RDS collection complete",
            extra={"account_id": account_id, "region": region, "count": len(nodes)},
        )

    except ClientError as exc:
        logger.error(
            "RDS collection failed",
            extra={"account_id": account_id, "region": region, "error": str(exc)},
        )

    return nodes
