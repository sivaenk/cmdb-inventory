"""
AWS session utilities — cross-account role assumption and identity verification.
"""
import boto3
from botocore.exceptions import ClientError

from aws_resource_collectors.utils.logger import get_logger

logger = get_logger(__name__)


def get_local_account_id() -> str | None:
    """
    Return the account ID of the current ambient credentials via sts:GetCallerIdentity.
    Used to detect the management/delegated-admin account so we skip AssumeRole for it.
    """
    try:
        sts = boto3.client("sts")
        return sts.get_caller_identity()["Account"]
    except ClientError as exc:
        logger.error("Failed to get local account ID", extra={"error": str(exc)})
        return None


def assume_role(
    account_id: str,
    role_name: str,
    session_name: str = "ResourceCollectorSession",
    external_id: str | None = None,
) -> boto3.Session | None:
    """
    Assume a cross-account IAM role via sts:AssumeRole.

    Parameters
    ----------
    account_id : str
        Target AWS account ID.
    role_name : str
        Name of the IAM role to assume.
    session_name : str
        Session name for the assumed role session.
    external_id : str | None
        Optional ExternalId for sts:AssumeRole trust policy condition.

    Returns
    -------
    boto3.Session | None
        A boto3 Session scoped to the assumed role, or None if assumption fails.
        On failure, logs the error and returns None so the caller can skip the
        account and continue processing others.
    """
    role_arn = f"arn:aws:iam::{account_id}:role/{role_name}"
    sts_client = boto3.client("sts")

    kwargs: dict = {"RoleArn": role_arn, "RoleSessionName": session_name}
    if external_id:
        kwargs["ExternalId"] = external_id

    try:
        response = sts_client.assume_role(**kwargs)
        creds = response["Credentials"]
        session = boto3.Session(
            aws_access_key_id=creds["AccessKeyId"],
            aws_secret_access_key=creds["SecretAccessKey"],
            aws_session_token=creds["SessionToken"],
        )
        logger.info("Assumed role successfully", extra={"account_id": account_id, "role_arn": role_arn})
        return session
    except ClientError as exc:
        logger.error(
            "Failed to assume role",
            extra={"account_id": account_id, "role_arn": role_arn, "error": str(exc)},
        )
        return None


def verify_identity(session: boto3.Session) -> str | None:
    """
    Verify the assumed identity via sts:GetCallerIdentity and return the account ID.

    Parameters
    ----------
    session : boto3.Session
        The assumed-role session to verify.

    Returns
    -------
    str | None
        The verified account ID string, or None if the call fails.
    """
    try:
        sts = session.client("sts")
        identity = sts.get_caller_identity()
        account_id = identity["Account"]
        logger.info("Identity verified", extra={"account_id": account_id, "arn": identity.get("Arn")})
        return account_id
    except ClientError as exc:
        logger.error("Failed to verify identity", extra={"error": str(exc)})
        return None
