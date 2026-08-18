"""
Route Table collector.
"""
import boto3
from botocore.exceptions import ClientError

from aws_resource_collectors.models.resource_node import ResourceNode
from aws_resource_collectors.utils.logger import get_logger

logger = get_logger(__name__)


def collect(session: boto3.Session, account_id: str, region: str) -> list[ResourceNode]:
    """
    Collect all Route Tables and return ResourceNode list.
    Stores empty SubnetIds list when no associations exist.
    """
    ec2 = session.client("ec2", region_name=region)
    nodes: list[ResourceNode] = []

    try:
        paginator = ec2.get_paginator("describe_route_tables")

        for page in paginator.paginate():
            for rt in page.get("RouteTables", []):
                rt_id = rt["RouteTableId"]
                tags = {t["Key"]: t["Value"] for t in rt.get("Tags", [])}

                # Extract associated subnet IDs (explicit associations only)
                subnet_ids = [
                    assoc["SubnetId"]
                    for assoc in rt.get("Associations", [])
                    if assoc.get("SubnetId")
                ]

                # Extract routes
                routes = [
                    {
                        "DestinationCidrBlock": r.get("DestinationCidrBlock") or r.get("DestinationIpv6CidrBlock"),
                        "Target": (
                            r.get("GatewayId")
                            or r.get("NatGatewayId")
                            or r.get("TransitGatewayId")
                            or r.get("VpcPeeringConnectionId")
                            or r.get("NetworkInterfaceId")
                            or r.get("InstanceId")
                            or "local"
                        ),
                        "State": r.get("State"),
                    }
                    for r in rt.get("Routes", [])
                ]

                metadata = {
                    "VpcId": rt.get("VpcId"),
                    "AssociatedSubnetIds": subnet_ids,
                    "Routes": routes,
                    "Tags": tags,
                }

                nodes.append(
                    ResourceNode(
                        ResourceId=rt_id,
                        ResourceType="RouteTable",
                        AccountId=account_id,
                        Region=region,
                        Metadata=metadata,
                        Relationships=[],
                    )
                )

        logger.info(
            "RouteTable collection complete",
            extra={"account_id": account_id, "region": region, "count": len(nodes)},
        )

    except ClientError as exc:
        logger.error(
            "RouteTable collection failed",
            extra={"account_id": account_id, "region": region, "error": str(exc)},
        )

    return nodes
