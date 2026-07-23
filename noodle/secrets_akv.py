"""Azure Key Vault secret loader.

Enterprise secret source: a vault + managed identity instead of secrets.env.
Enabled by NOODLE_KEYVAULT_URL; auth uses DefaultAzureCredential (managed
identity on Azure agents, `az login` / env locally). Vault secret names map to
env vars: dashes → underscores, uppercased ("sauce-password" → SAUCE_PASSWORD),
since Key Vault names can't contain underscores.
"""
import os

from noodle import log


def _normalize(name: str) -> str:
    return name.replace("-", "_").upper()


def load_into_environ(vault_url: str, override: bool = True) -> int:
    """Fetch every secret in the vault into os.environ. Returns the count.
    Real azure clients are created here so the module imports without the SDK."""
    from azure.identity import DefaultAzureCredential
    from azure.keyvault.secrets import SecretClient

    client = SecretClient(vault_url=vault_url, credential=DefaultAzureCredential())
    return _apply(client, override)


def _apply(client, override: bool = True) -> int:
    """Pure-ish merge step: takes any client exposing list_properties_of_secrets()
    + get_secret(name).value, writes to os.environ. Split out so it's testable
    with a fake client (no Azure, no network)."""
    count = 0
    for prop in client.list_properties_of_secrets():
        key = _normalize(prop.name)
        if not override and key in os.environ:
            continue
        value = client.get_secret(prop.name).value
        os.environ[key] = value
        log.register_secret(value)          # NOOD_0118 — scrub from all log output
        count += 1
    return count
