"""
EC2 Instance collector.
"""
import boto3
from botocore.exceptions import ClientError

from aws_resource_collectors.models.resource_node import ResourceNode
from aws_resource_collectors.utils.logger import get_logger

logger = get_logger(__name__)


def collect(session: boto3.Session, account_id: str, region: str) -> list[ResourceNode]:
    """
    Collect all EC2 instances (all states) and return ResourceNode list.
    Enriches each instance with AMI name via ec2:DescribeImages.
    """
    ec2 = session.client("ec2", region_name=region)
    nodes: list[ResourceNode] = []

    try:
        paginator = ec2.get_paginator("describe_instances")
        instances = []
        ami_ids: set[str] = set()

        for page in paginator.paginate():
            for reservation in page.get("Reservations", []):
                for inst in reservation.get("Instances", []):
                    instances.append(inst)
                    if inst.get("ImageId"):
                        ami_ids.add(inst["ImageId"])

        # Batch-fetch AMI names
        ami_name_map: dict[str, str] = {}
        if ami_ids:
            try:
                ami_ids_list = list(ami_ids)
                # DescribeImages accepts up to 200 IDs per call
                for i in range(0, len(ami_ids_list), 200):
                    batch = ami_ids_list[i:i + 200]
                    resp = ec2.describe_images(ImageIds=batch)
                    for image in resp.get("Images", []):
                        ami_name_map[image["ImageId"]] = image.get("Name", "")
            except ClientError as exc:
                logger.warning(
                    "Failed to describe AMI images",
                    extra={"account_id": account_id, "region": region, "error": str(exc)},
                )

        for inst in instances:
            instance_id = inst["InstanceId"]
            ami_id = inst.get("ImageId", "")
            tags = {t["Key"]: t["Value"] for t in inst.get("Tags", [])}

            # Extract security group IDs
            sg_ids = [sg["GroupId"] for sg in inst.get("SecurityGroups", [])]

            # Extract block device mappings (needed for EC2→EBS enrichment)
            bdm = [
                {
                    "DeviceName": b["DeviceName"],
                    "VolumeId": b.get("Ebs", {}).get("VolumeId"),
                    "Status": b.get("Ebs", {}).get("Status"),
                    "DeleteOnTermination": b.get("Ebs", {}).get("DeleteOnTermination"),
                }
                for b in inst.get("BlockDeviceMappings", [])
            ]

            iam_profile = inst.get("IamInstanceProfile", {}).get("Arn")

            metadata = {
                "InstanceType": inst.get("InstanceType"),
                "State": inst.get("State", {}).get("Name"),
                "Platform": inst.get("Platform", "linux"),
                "AmiId": ami_id,
                "AmiName": ami_name_map.get(ami_id, ""),
                "VpcId": inst.get("VpcId"),
                "SubnetId": inst.get("SubnetId"),
                "AvailabilityZone": inst.get("Placement", {}).get("AvailabilityZone"),
                "PrivateIp": inst.get("PrivateIpAddress"),
                "PublicIp": inst.get("PublicIpAddress"),
                "IamInstanceProfile": iam_profile,
                "LaunchTime": inst.get("LaunchTime", "").isoformat() if hasattr(inst.get("LaunchTime", ""), "isoformat") else inst.get("LaunchTime"),
                "SecurityGroupIds": sg_ids,
                "BlockDeviceMappings": bdm,
                "Tags": tags,
            }

            nodes.append(
                ResourceNode(
                    ResourceId=instance_id,
                    ResourceType="EC2",
                    AccountId=account_id,
                    Region=region,
                    Metadata=metadata,
                    Relationships=[],
                )
            )

        logger.info(
            "EC2 collection complete",
            extra={"account_id": account_id, "region": region, "count": len(nodes)},
        )

    except ClientError as exc:
        logger.error(
            "EC2 collection failed",
            extra={"account_id": account_id, "region": region, "error": str(exc)},
        )

    return nodes
