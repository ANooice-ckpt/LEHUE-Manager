from __future__ import annotations

from app.core import config


def oss_endpoint(*, public: bool = False) -> str:
    settings = config.settings
    if public and settings.oss_public_endpoint:
        return settings.oss_public_endpoint
    if not public and settings.oss_endpoint:
        return settings.oss_endpoint
    return f"https://oss-{settings.oss_region}.aliyuncs.com" if settings.oss_region else ""


def oss_auth():
    settings = config.settings
    try:
        import oss2
        from oss2.credentials import Credentials
    except ImportError as exc:
        raise RuntimeError("OSS access requires oss2") from exc

    if settings.oss_credential_mode == "ecs_ram_role":
        try:
            from alibabacloud_credentials.client import Client
            from alibabacloud_credentials.models import Config
        except ImportError as exc:
            raise RuntimeError("ECS RAM role access requires alibabacloud_credentials") from exc
        options = {"type": "ecs_ram_role"}
        if settings.oss_role_name:
            options["role_name"] = settings.oss_role_name
        client = Client(Config(**options))
    else:
        if not settings.oss_access_key_id or not settings.oss_access_key_secret:
            raise RuntimeError("OSS access_key mode requires OSS_ACCESS_KEY_ID and OSS_ACCESS_KEY_SECRET")

        class StaticClient:
            def get_credential(self):
                return type("Credential", (), {
                    "access_key_id": settings.oss_access_key_id,
                    "access_key_secret": settings.oss_access_key_secret,
                    "security_token": "",
                })()

        client = StaticClient()

    class Provider(oss2.CredentialsProvider):
        def get_credentials(self):
            credential = client.get_credential()
            return Credentials(
                credential.access_key_id,
                credential.access_key_secret,
                credential.security_token or "",
            )

    return oss2.ProviderAuthV4(Provider())


def oss_bucket(bucket_name: str, *, public: bool = False):
    settings = config.settings
    endpoint = oss_endpoint(public=public)
    if not bucket_name or not endpoint or not settings.oss_region:
        raise RuntimeError("OSS access requires bucket, OSS_REGION and endpoint")
    import oss2
    return oss2.Bucket(oss_auth(), endpoint, bucket_name, region=settings.oss_region)
