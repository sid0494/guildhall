"""Connections to the AWS services the platform uses: SQS and S3 (docs/adr/0032).

Locally every client points at moto through `GUILDHALL_AWS_ENDPOINT_URL` with
placeholder keys; on AWS both are unset and the pod's role provides
credentials. Queues and buckets are created outside the worker: by Terraform
on AWS and by `guildhall.platform.dev_aws` locally.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from aiobotocore.session import get_session
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

if TYPE_CHECKING:  # type stubs are a dev dependency, absent from the image
    from types_aiobotocore_s3 import S3Client
    from types_aiobotocore_sqs import SQSClient


class AwsSettings(BaseSettings):
    """Where AWS is and how to sign in; extended by each service's settings."""

    model_config = SettingsConfigDict(
        env_prefix="GUILDHALL_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    aws_region: str = "us-east-1"
    aws_endpoint_url: str | None = None
    """Set locally to the moto endpoint; unset on AWS."""
    aws_access_key_id: SecretStr | None = None
    """Set locally (any value works with moto); on AWS the pod's role is used."""
    aws_secret_access_key: SecretStr | None = None

    def client_arguments(self) -> dict[str, Any]:
        """Keyword arguments for `create_client`, secrets unwrapped."""
        key_id, secret = self.aws_access_key_id, self.aws_secret_access_key
        return {
            "region_name": self.aws_region,
            "endpoint_url": self.aws_endpoint_url,
            "aws_access_key_id": key_id.get_secret_value() if key_id else None,
            "aws_secret_access_key": secret.get_secret_value() if secret else None,
        }


@asynccontextmanager
async def open_sqs(settings: AwsSettings) -> AsyncIterator[SQSClient]:
    """An SQS client for the configured region, endpoint and credentials."""
    async with get_session().create_client(
        "sqs", **settings.client_arguments()
    ) as client:
        yield client


@asynccontextmanager
async def open_s3(settings: AwsSettings) -> AsyncIterator[S3Client]:
    """An S3 client for the configured region, endpoint and credentials."""
    async with get_session().create_client(
        "s3", **settings.client_arguments()
    ) as client:
        yield client
