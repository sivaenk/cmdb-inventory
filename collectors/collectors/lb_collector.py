"""
Load Balancer collector — ALB, NLB (elbv2) and CLB (elb).
"""
import boto3
from botocore.exceptions import ClientError

from aws_resource_collectors.models.resource_node import ResourceNode
from aws_resource_collectors.utils.logger import get_logger

logger = get_logger(__name__)


def _get_elbv2_tags(client, arns: list[str]) -> dict[str, dict]:
    """Fetch tags for a batch of ALB/NLB ARNs. Returns {arn: {key: value}}."""
    tag_map: dict[str, dict] = {}
    # DescribeTags accepts up to 20 ARNs per call
    for i in range(0, len(arns), 20):
        batch = arns[i:i + 20]
        try:
            resp = client.describe_tags(ResourceArns=batch)
            for td in resp.get("TagDescriptions", []):
                tag_map[td["ResourceArn"]] = {t["Key"]: t["Value"] for t in td.get("Tags", [])}
        except ClientError as exc:
            logger.warning("Failed to fetch ELBv2 tags", extra={"error": str(exc)})
    return tag_map


def _get_clb_tags(client, names: list[str]) -> dict[str, dict]:
    """Fetch tags for a batch of CLB names. Returns {name: {key: value}}."""
    tag_map: dict[str, dict] = {}
    for i in range(0, len(names), 20):
        batch = names[i:i + 20]
        try:
            resp = client.describe_tags(LoadBalancerNames=batch)
            for td in resp.get("TagDescriptions", []):
                tag_map[td["LoadBalancerName"]] = {t["Key"]: t["Value"] for t in td.get("Tags", [])}
        except ClientError as exc:
            logger.warning("Failed to fetch CLB tags", extra={"error": str(exc)})
    return tag_map


def collect(session: boto3.Session, account_id: str, region: str) -> list[ResourceNode]:
    """
    Collect ALB/NLB (elbv2) and CLB (elb) load balancers.
    Returns ResourceNode list with empty Relationships.
    """
    nodes: list[ResourceNode] = []

    # --- ALB / NLB (elbv2) ---
    try:
        elbv2 = session.client("elbv2", region_name=region)
        paginator = elbv2.get_paginator("describe_load_balancers")
        v2_lbs = []
        for page in paginator.paginate():
            v2_lbs.extend(page.get("LoadBalancers", []))

        arns = [lb["LoadBalancerArn"] for lb in v2_lbs]
        tag_map = _get_elbv2_tags(elbv2, arns) if arns else {}

        for lb in v2_lbs:
            arn = lb["LoadBalancerArn"]
            lb_type = lb.get("Type", "").upper()  # application → ALB, network → NLB
            if lb_type == "APPLICATION":
                lb_type = "ALB"
            elif lb_type == "NETWORK":
                lb_type = "NLB"
            elif lb_type == "GATEWAY":
                lb_type = "GWLB"

            metadata = {
                "LoadBalancerArn": arn,
                "DNSName": lb.get("DNSName"),
                "Type": lb_type,
                "Scheme": lb.get("Scheme"),
                "VpcId": lb.get("VpcId"),
                "State": lb.get("State", {}).get("Code"),
                "Tags": tag_map.get(arn, {}),
            }

            nodes.append(
                ResourceNode(
                    ResourceId=arn,
                    ResourceType=lb_type,
                    AccountId=account_id,
                    Region=region,
                    Metadata=metadata,
                    Relationships=[],
                )
            )

    except ClientError as exc:
        logger.error(
            "ELBv2 collection failed",
            extra={"account_id": account_id, "region": region, "error": str(exc)},
        )

    # --- CLB (elb v1) ---
    try:
        elb = session.client("elb", region_name=region)
        paginator = elb.get_paginator("describe_load_balancers")
        clb_lbs = []
        for page in paginator.paginate():
            clb_lbs.extend(page.get("LoadBalancerDescriptions", []))

        names = [lb["LoadBalancerName"] for lb in clb_lbs]
        tag_map = _get_clb_tags(elb, names) if names else {}

        for lb in clb_lbs:
            name = lb["LoadBalancerName"]
            metadata = {
                "LoadBalancerName": name,
                "DNSName": lb.get("DNSName"),
                "Type": "CLB",
                "Scheme": lb.get("Scheme"),
                "VpcId": lb.get("VPCId"),
                "State": "active",  # CLB has no explicit state field
                "Tags": tag_map.get(name, {}),
            }

            nodes.append(
                ResourceNode(
                    ResourceId=name,
                    ResourceType="CLB",
                    AccountId=account_id,
                    Region=region,
                    Metadata=metadata,
                    Relationships=[],
                )
            )

    except ClientError as exc:
        logger.error(
            "CLB collection failed",
            extra={"account_id": account_id, "region": region, "error": str(exc)},
        )

    logger.info(
        "LB collection complete",
        extra={"account_id": account_id, "region": region, "count": len(nodes)},
    )
    return nodes
