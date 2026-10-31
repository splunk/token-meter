"""Runtime-neutral redaction predicates for text bound for Work insights.

Adapters that read a user's own typed request for the Work classifier drop the
whole request when it carries a credential-shaped token, a host-like value, or
an account shape; they never partially scrub it. The predicates live here so
every adapter shares one copy of the checks that must never narrow.
"""

import re


# Credential prefixes that are distinctive on their own. Matched at a token
# boundary, case-insensitively, wherever they appear in the value.
_SECRET_TOKEN_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])(?:"
    r"sk-|ghp_|gho_|ghu_|ghs_|ghr_|github_pat_|xox[abposr]-|glpat-"
    r"|(?:sk|pk|rk)_(?:live|test)_"
    r")",
    re.IGNORECASE,
)
# Short or word-like prefixes (Hugging Face, npm, xAI, Google API keys) count
# only when followed by a long unbroken alphanumeric run. Real keys carry
# 30+ characters there; a 16-character floor keeps ordinary names such as
# ``xai-researcher`` or ``hf_transformers`` while rejecting key material.
_SHORT_SECRET_PREFIX_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])(?:hf_|npm_|xai-|aiza)[A-Za-z0-9]{16,}",
    re.IGNORECASE,
)
_AWS_ACCESS_KEY_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])(?:AKIA|ASIA)[0-9A-Z]{12,}", re.IGNORECASE,
)
_JWT_PATTERN = re.compile(
    r"eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*"
)
_HOST_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_IPV4 = r"\d{1,3}(?:\.\d{1,3}){3}"
_DOTTED_HOST = r"(?:%s\.)+[a-z]{2,63}" % _HOST_LABEL
_COMMON_HOST_SUFFIXES = frozenset((
    "com", "net", "org", "io", "dev", "app", "cloud", "local", "lan",
    "internal", "corp", "intranet", "localdomain", "home", "test",
    "example", "invalid", "localhost",
))


def has_secret_token(text):
    """Return whether text carries a credential-shaped token anywhere."""
    return bool(
        _SECRET_TOKEN_PATTERN.search(text)
        or _SHORT_SECRET_PREFIX_PATTERN.search(text)
        or _AWS_ACCESS_KEY_PATTERN.search(text)
        or _JWT_PATTERN.search(text)
    )


def is_host_like(text):
    """Return whether the whole value is a hostname, IP, or host:port."""
    lowered = text.lower()
    if re.fullmatch(r"(?:%s|%s|localhost):\d{1,5}" % (_IPV4, _DOTTED_HOST), lowered):
        return True
    if lowered == "localhost" or re.fullmatch(_IPV4, lowered):
        return True
    if re.fullmatch(r"%s:\d{4,5}" % _HOST_LABEL, lowered):
        # Single-label host with a port-shaped tag such as gateway:8080.
        # Model tags (llama3.1:8b, model:latest, ...-v1:0) do not match.
        return True
    if re.fullmatch(_DOTTED_HOST, lowered):
        # Bare names: require a common host suffix or three or more labels so
        # dotted vendor ids keep working.
        labels = lowered.split(".")
        return labels[-1] in _COMMON_HOST_SUFFIXES or len(labels) >= 3
    return False


# The run that may follow an '@' as a host candidate: a dotted name, an IPv4
# address, or a single-label host with a port. It ends at the first character
# that cannot be part of one of those, so prose, a path, or a trailing sentence
# mark bounds the token.
_AT_HOST_RUN = re.compile(r"[A-Za-z0-9.:-]*")


def _account_domain(text, position):
    """Return the bounded host candidate that follows one '@'."""
    match = _AT_HOST_RUN.match(text, position + 1)
    return match.group(0).rstrip(".") if match else ""


def has_account_at(text):
    """Reject user@host shapes while keeping @scope/ and model@version ids.

    The check is bounded to the address token that follows each '@'. A
    mid-sentence '@', a leading namespace (``@cf/``, ``@types/``), and a
    version or tag (``model@latest``, ``model@20240101``) are not account
    shapes; only a value that reads as a host immediately after the '@' is.
    """
    for position, char in enumerate(text):
        if char == "@" and is_host_like(_account_domain(text, position)):
            return True
    return False
