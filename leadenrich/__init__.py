"""lead-enrich: deterministic record resolution with provenance.

>>> from leadenrich import Record, resolve
>>> a = Record.build({"email": "j@acme.com", "name": "jane doe"}, "crm")
>>> b = Record.build({"email": "J@Acme.com", "name": "Jane Doe"}, "ads")
>>> merged = resolve([a, b])[0]
>>> merged.fields["name"]
'Jane Doe'
>>> merged.provenance["name"]
'ads'
"""

from .normalize import (
    DISPOSABLE_DOMAINS,
    FIELD_NORMALISERS,
    InvalidValue,
    is_disposable_domain,
    is_role_address,
    normalize_company,
    normalize_domain,
    normalize_email,
    normalize_fields,
    normalize_name,
    normalize_phone,
    registrable_domain,
)
from .resolve import (
    Comparison,
    Conflict,
    MergedRecord,
    Record,
    blocking_keys,
    compare,
    match_pairs,
    resolve,
)
from .similarity import best_similarity, jaro, jaro_winkler, token_set_ratio

__all__ = [
    "DISPOSABLE_DOMAINS",
    "FIELD_NORMALISERS",
    "Comparison",
    "Conflict",
    "InvalidValue",
    "MergedRecord",
    "Record",
    "best_similarity",
    "blocking_keys",
    "compare",
    "is_disposable_domain",
    "is_role_address",
    "jaro",
    "jaro_winkler",
    "match_pairs",
    "normalize_company",
    "normalize_domain",
    "normalize_email",
    "normalize_fields",
    "normalize_name",
    "normalize_phone",
    "registrable_domain",
    "resolve",
    "token_set_ratio",
]

__version__ = "1.0.0"
