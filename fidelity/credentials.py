"""
Credential management for Fidelity API.

Pulls login credentials from AWS Secrets Manager for automated,
headless login via FidelityAutomation.

Secrets expected in AWS SM (us-east-1):
  - fidelity/username
  - fidelity/password
  - fidelity/totp_secret

Usage:
    from fidelity.credentials import get_credentials, login_and_create_client

    # Just get credentials
    creds = get_credentials()
    print(creds["username"])

    # Full automated login -> API client
    client = login_and_create_client()
    print(client.get_spx_price())
"""

import boto3
from botocore.exceptions import ClientError


DEFAULT_REGION = "us-east-1"
DEFAULT_PROFILE = "personal"

SECRET_NAMES = {
    "username": "fidelity/username",
    "password": "fidelity/password",
    "totp_secret": "fidelity/totp_secret",
}


def _get_sm_client(region: str = DEFAULT_REGION, profile: str = DEFAULT_PROFILE):
    """Create a Secrets Manager client."""
    session = boto3.Session(profile_name=profile, region_name=region)
    return session.client("secretsmanager")


def get_secret(name: str, region: str = DEFAULT_REGION, profile: str = DEFAULT_PROFILE) -> str:
    """Fetch a single secret value from AWS Secrets Manager."""
    client = _get_sm_client(region, profile)
    try:
        resp = client.get_secret_value(SecretId=name)
        return resp["SecretString"]
    except ClientError as e:
        raise ValueError(f"Failed to fetch secret '{name}': {e}")


def get_credentials(region: str = DEFAULT_REGION, profile: str = DEFAULT_PROFILE) -> dict:
    """Fetch all Fidelity credentials from AWS Secrets Manager.

    Returns
    -------
    dict with keys: username, password, totp_secret
    """
    client = _get_sm_client(region, profile)
    creds = {}
    for key, secret_name in SECRET_NAMES.items():
        try:
            resp = client.get_secret_value(SecretId=secret_name)
            creds[key] = resp["SecretString"]
        except ClientError as e:
            raise ValueError(f"Failed to fetch secret '{secret_name}': {e}")
    return creds


def login_and_create_client(
    headless: bool = True,
    save_state: bool = True,
    region: str = DEFAULT_REGION,
    profile: str = DEFAULT_PROFILE,
):
    """Automated login: pull creds from SM, login via Playwright, return API client.

    Parameters
    ----------
    headless : bool
        Run browser headless (default True for production).
    save_state : bool
        Save cookies for faster subsequent logins.
    region : str
        AWS region for Secrets Manager.
    profile : str
        AWS CLI profile name.

    Returns
    -------
    tuple of (FidelityAPIClient, FidelityAutomation)
        The API client with fresh cookies, and the browser instance
        (caller should call automation.close_browser() when done).
    """
    from fidelity.fidelity import FidelityAutomation
    from fidelity.api_client import FidelityAPIClient

    creds = get_credentials(region, profile)

    automation = FidelityAutomation(headless=headless, save_state=save_state)
    step1, step2 = automation.login(
        username=creds["username"],
        password=creds["password"],
        totp_secret=creds["totp_secret"],
        save_device=False,
    )

    if not (step1 and step2):
        automation.close_browser()
        raise RuntimeError(
            f"Fidelity login failed: step1={step1}, step2={step2}. "
            "Check credentials in AWS Secrets Manager."
        )

    client = FidelityAPIClient.from_automation(automation)
    return client, automation
