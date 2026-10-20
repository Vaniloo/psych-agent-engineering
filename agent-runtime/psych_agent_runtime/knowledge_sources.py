"""Reviewed text-source identities; a license label alone grants no trust."""

from urllib.parse import urlparse

SOURCES = {
    "www.nimh.nih.gov": {
        "id": "nimh", "license": "public-domain-nimh", "paths": ("/health/",),
        "publisher": "National Institute of Mental Health (NIMH)",
        "policy": "https://www.nimh.nih.gov/site-info/policies",
    },
    "www.nhlbi.nih.gov": {
        "id": "nhlbi", "license": "public-domain-nhlbi", "paths": ("/health/",),
        "publisher": "National Heart, Lung, and Blood Institute (NHLBI)",
        "policy": "https://www.nhlbi.nih.gov/about/contact/trademark-branding-and-logo",
    },
    "www.nccih.nih.gov": {
        "id": "nccih", "license": "public-domain-nccih", "paths": ("/health/",),
        "publisher": "National Center for Complementary and Integrative Health (NCCIH)",
        "policy": "https://www.nccih.nih.gov/tools/privacy",
    },
    "www.cdc.gov": {
        "id": "cdc", "license": "public-domain-cdc",
        "paths": ("/mental-health/", "/emotional-well-being/", "/social-connectedness/", "/sleep/"),
        "publisher": "Centers for Disease Control and Prevention (CDC)",
        "policy": "https://www.cdc.gov/other/agencymaterials.html",
    },
}


def source_policy(url):
    try:
        parsed = urlparse(url)
        policy = SOURCES.get(parsed.hostname)
        if (parsed.scheme != "https" or not policy or parsed.username or parsed.password
                or parsed.port not in {None, 443} or parsed.query or parsed.fragment
                or not parsed.path.startswith(policy["paths"])
                or any(segment in {".", ".."} or "%" in segment for segment in parsed.path.split("/"))):
            return None
        return policy
    except (ValueError, TypeError):
        return None


def licensed_source(metadata):
    policy = source_policy(metadata.get("source", ""))
    if not policy or metadata.get("license") != policy["license"]:
        return False
    # Preserve the original frozen NIMH corpus, which predates these fields.
    if policy["id"] == "nimh" and "licenseSource" not in metadata:
        return True
    return (metadata.get("licenseSource") == policy["policy"]
            and metadata.get("publisher") == policy["publisher"])
