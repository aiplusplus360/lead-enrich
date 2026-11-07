"""Field normalisation for people and company records.

Normalisation is where enrichment pipelines quietly destroy data:

* stripping ``+tags`` from every address breaks plus-addressing on domains that
  use it as a real mailbox separator, so it is only applied where the provider
  documents it (Gmail);
* ``www.``, a port, a trailing dot and an IDN label are four spellings of one
  domain, and any comparison that misses one of them silently reports "no
  match";
* a phone number without a country code is not a number, it is a guess, and
  guessing silently is worse than reporting it.

Every function here either returns a canonical value or raises ``InvalidValue``
with a reason you can show a human. Nothing is dropped without a record of why.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

__all__ = [
    "InvalidValue",
    "normalize_email",
    "normalize_phone",
    "normalize_domain",
    "normalize_name",
    "normalize_company",
    "normalize_fields",
    "is_role_address",
    "is_disposable_domain",
    "registrable_domain",
    "FIELD_NORMALISERS",
]

_EMAIL = re.compile(
    r"^[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?"
    r"(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$"
)
_DOMAIN = re.compile(
    r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$"
)

# Providers that document dot and plus handling. Applying these rules to other
# domains changes the mailbox, so the canonical form is provider-specific.
_GMAIL = {"gmail.com", "googlemail.com"}

ROLE_LOCALS = frozenset(
    """admin billing contact careers hello help hr info jobs legal mail marketing
    noreply no-reply office postmaster press privacy sales security support team
    webmaster accounts""".split()
)

DISPOSABLE_DOMAINS = frozenset(
    """10minutemail.com 20minutemail.com discard.email discardmail.com
    guerrillamail.com guerrillamail.net maildrop.cc mailinator.com mailnesia.com
    mintemail.com mohmal.com sharklasers.com spam4.me temp-mail.org tempail.com
    tempmail.com tempmail.net throwawaymail.com trashmail.com yopmail.com
    yopmail.fr fakeinbox.com getnada.com""".split()
)

# Second-level labels that are part of the public suffix rather than the name.
_SECOND_LEVEL = frozenset("co com org net ac gov edu or ne go in".split())

LEGAL_SUFFIXES = (
    "incorporated", "corporation", "limited", "holdings", "company", "group",
    "gmbh", "sarl", "srl", "plc", "llc", "llp", "ltd", "inc", "corp", "co",
    "pvt", "private", "pty", "bv", "nv", "ag", "ab", "oy", "as", "sa", "kg",
)
_NAME_TITLES = frozenset("mr mrs ms miss dr prof sir madam mx".split())
_NAME_SUFFIXES = frozenset("jr sr ii iii iv phd md esq".split())

# Country dialling codes with the national-number lengths we can verify. Kept
# small on purpose: an unknown region is reported, not guessed.
DIAL_CODES = {
    "US": ("1", (10,)),
    "CA": ("1", (10,)),
    "GB": ("44", (10,)),
    "DE": ("49", (10, 11)),
    "FR": ("33", (9,)),
    "IN": ("91", (10,)),
    "AU": ("61", (9,)),
    "BR": ("55", (10, 11)),
    "NL": ("31", (9,)),
    "ES": ("34", (9,)),
    "IT": ("39", (9, 10)),
    "AE": ("971", (9,)),
    "SG": ("65", (8,)),
    "PK": ("92", (10,)),
    "BD": ("880", (10,)),
}


class InvalidValue(ValueError):
    """Raised with a human-readable reason when a value cannot be canonicalised."""


def is_disposable_domain(domain: str) -> bool:
    return domain.lower() in DISPOSABLE_DOMAINS


def is_role_address(email: str) -> bool:
    local = email.split("@", 1)[0]
    return local.split("+", 1)[0].split(".", 1)[0] in ROLE_LOCALS or local in ROLE_LOCALS


def registrable_domain(domain: str) -> str:
    """Best-effort eTLD+1.

    This is a heuristic, not the Public Suffix List: it handles the common
    ``example.co.uk`` case and nothing more. Production systems should use a
    real PSL, and this docstring says so rather than pretending otherwise.
    """
    labels = domain.split(".")
    if len(labels) <= 2:
        return domain
    if labels[-2] in _SECOND_LEVEL and len(labels[-1]) == 2:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def normalize_domain(raw: str) -> str:
    value = (raw or "").strip().lower()
    if not value:
        raise InvalidValue("empty domain")
    value = re.sub(r"^[a-z][a-z0-9+.-]*://", "", value)
    value = value.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    if "@" in value:
        value = value.rsplit("@", 1)[-1]
    value = value.split(":", 1)[0]
    value = value.rstrip(".")
    if value.startswith("www."):
        value = value[4:]
    if not value:
        raise InvalidValue("empty domain after stripping scheme and www")
    if not value.isascii():
        try:
            value = value.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise InvalidValue(f"invalid international domain: {exc}") from exc
    if not _DOMAIN.match(value):
        raise InvalidValue(f"not a domain: {raw!r}")
    return value


def normalize_email(
    raw: str, *, provider_canonical: bool = True, strip_subaddress: bool = False
) -> str:
    value = (raw or "").strip().strip("<>").lower()
    if value.startswith("mailto:"):
        value = value[7:]
    if not value:
        raise InvalidValue("empty email")
    if value.count("@") != 1:
        raise InvalidValue(f"expected exactly one @: {raw!r}")
    local, domain = value.split("@")
    if not local or not domain:
        raise InvalidValue(f"incomplete email: {raw!r}")
    try:
        domain = normalize_domain(domain)
    except InvalidValue as exc:
        raise InvalidValue(f"invalid email domain: {exc}") from exc
    if not _EMAIL.match(f"{local}@{domain}"):
        raise InvalidValue(f"not an email address: {raw!r}")
    if provider_canonical and domain in _GMAIL:
        # Gmail ignores dots in the local part (documented by Google) and treats
        # everything after "+" as a sub-address. Dots are safe to drop for
        # matching; the sub-address is only dropped on request, because it
        # distinguishes two real mailboxes.
        local = local.replace(".", "")
        if strip_subaddress:
            local = local.split("+", 1)[0]
    return f"{local}@{domain}"


def normalize_phone(raw: str, *, region: Optional[str] = None) -> str:
    value = (raw or "").strip()
    if not value:
        raise InvalidValue("empty phone")
    plus = value.startswith("+") or value.startswith("00")
    digits = re.sub(r"\D", "", value)
    if not digits:
        raise InvalidValue(f"no digits in phone: {raw!r}")
    if value.startswith("00"):
        digits = digits[2:]

    if not plus:
        if region is None:
            raise InvalidValue(
                f"phone {raw!r} has no country code and no region was supplied"
            )
        region = region.upper()
        if region not in DIAL_CODES:
            raise InvalidValue(f"unsupported region {region!r} for phone {raw!r}")
        code, national_lengths = DIAL_CODES[region]
        if digits.startswith(code) and len(digits) - len(code) in national_lengths:
            pass  # the number already carries the country code
        else:
            if digits.startswith("0"):
                digits = digits.lstrip("0")
            if len(digits) not in national_lengths:
                raise InvalidValue(
                    f"national number has {len(digits)} digits, expected one of "
                    f"{national_lengths} for {region}"
                )
            digits = code + digits

    if not 8 <= len(digits) <= 15:
        raise InvalidValue(f"phone has {len(digits)} digits, expected 8..15")
    if digits.startswith("0"):
        raise InvalidValue(f"international number cannot start with 0: {raw!r}")
    return "+" + digits


def normalize_name(raw: str) -> str:
    value = (raw or "").strip()
    if not value:
        raise InvalidValue("empty name")
    value = value.replace("\u2019", "'").replace("\u2018", "'")
    tokens: List[str] = []
    for token in re.split(r"\s+", value):
        cleaned = re.sub(r"[^\w'\-. ]", "", token, flags=re.UNICODE)
        cleaned = re.sub(r"[\d_]", "", cleaned).strip(".,")
        if not cleaned:
            continue
        lowered = cleaned.lower().strip(".")
        if lowered in _NAME_TITLES:
            continue
        if not tokens and lowered in _NAME_SUFFIXES:
            continue
        if tokens and lowered in _NAME_SUFFIXES:
            continue
        tokens.append(cleaned)
    if not tokens:
        raise InvalidValue(f"no name tokens left in {raw!r}")
    formatted = []
    for token in tokens:
        if "-" in token:
            formatted.append("-".join(p[:1].upper() + p[1:].lower() for p in token.split("-")))
        elif "'" in token:
            head, _, tail = token.partition("'")
            formatted.append(head[:1].upper() + head[1:].lower() + "'" + tail[:1].upper() + tail[1:].lower())
        else:
            formatted.append(token[:1].upper() + token[1:].lower())
    return " ".join(formatted)


def normalize_company(raw: str) -> str:
    value = (raw or "").strip()
    if not value:
        raise InvalidValue("empty company")
    value = value.replace("&", " and ")
    value = re.sub(r"[^A-Za-z0-9 .\-]", " ", value)
    tokens = [t for t in re.split(r"\s+", value) if t]
    while tokens and tokens[-1].lower().strip(".") in LEGAL_SUFFIXES:
        tokens.pop()
    if not tokens:
        raise InvalidValue(f"company name is only legal suffix: {raw!r}")
    return " ".join(tokens).strip()


FIELD_NORMALISERS = {
    "email": normalize_email,
    "domain": normalize_domain,
    "phone": normalize_phone,
    "name": normalize_name,
    "company": normalize_company,
}

_ALIASES = {
    "e-mail": "email",
    "mail": "email",
    "website": "domain",
    "url": "domain",
    "phone_number": "phone",
    "tel": "phone",
    "mobile": "phone",
    "full_name": "name",
    "fullname": "name",
    "contact": "name",
    "organization": "company",
    "organisation": "company",
    "org": "company",
}


@dataclass
class FieldIssue:
    field: str
    raw: str
    reason: str

    def as_dict(self) -> Dict[str, str]:
        return {"field": self.field, "raw": self.raw, "reason": self.reason}


def normalize_fields(
    raw: Dict[str, object],
    *,
    region: Optional[str] = None,
) -> Tuple[Dict[str, str], List[FieldIssue]]:
    """Canonicalise a flat record. Unknown fields pass through untouched."""
    out: Dict[str, str] = {}
    issues: List[FieldIssue] = []

    for key, value in raw.items():
        if value is None:
            continue
        field = _ALIASES.get(str(key).strip().lower(), str(key).strip().lower())
        text = str(value).strip()
        if not text:
            continue
        normaliser = FIELD_NORMALISERS.get(field)
        if normaliser is None:
            out[field] = text
            continue
        try:
            if field == "phone":
                out[field] = normaliser(text, region=region)  # type: ignore[call-arg]
            else:
                out[field] = normaliser(text)
        except InvalidValue as exc:
            issues.append(FieldIssue(field=field, raw=text, reason=str(exc)))
    return out, issues
