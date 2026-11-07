# lead-enrich

**Deterministic lead de-duplication with provenance and explicit conflicts.** Normalises emails, phones, domains, names and companies; matches records with blocking plus fuzzy scoring; and never silently overwrites a field, because a merge you cannot audit is a merge you cannot trust.

Zero dependencies. Standard library only. Python 3.9+.

```console
$ leadenrich merge examples/crm.json examples/ads.json --source-order crm,ads --region US
{
  "input_records": 6,
  "merged_entities": 4,
  "needs_review": 3,
  "with_conflicts": 2,
  "field_issues": 0,
  "threshold": 0.82
}
```

---

## Four ways a dedupe pipeline quietly loses data

Every one of these is a normalisation or matching decision that looks harmless in code review and shows up six months later as a duplicate contact nobody can explain.

### 1. Stripping `+tags` from every address

Plus-addressing is documented Gmail behaviour. On a domain where it is not, `jane+crm@acme.com` and `jane@acme.com` may be two different mailboxes, and collapsing them merges two people.

`normalize_email` only rewrites the parts a provider documents: Gmail dots are insignificant, so they are dropped for matching; the sub-address is kept unless you explicitly pass `strip_subaddress=True`.

```python
>>> from leadenrich import normalize_email
>>> normalize_email("J.Doe@Gmail.com")
'jdoe@gmail.com'
>>> normalize_email("j.doe+news@gmail.com")
'jdoe+news@gmail.com'
>>> normalize_email("first.last@outlook.com")
'first.last@outlook.com'
```

### 2. Treating four spellings of one domain as four domains

`https://www.Example.com:443/path`, `www.example.com.`, `EXAMPLE.COM` and `example.com:8080` are one domain. Any comparison that misses one normalisation reports "no match" and creates a duplicate.

```python
>>> from leadenrich import normalize_domain, registrable_domain
>>> normalize_domain("https://www.Example.com:443/path?q=1")
'example.com'
>>> registrable_domain("shop.example.co.uk")
'example.co.uk'
```

`registrable_domain` is a heuristic, not the Public Suffix List. Use a real PSL in production; this library says so instead of pretending.

### 3. Guessing a country code

A phone number without a country code is not a number, it is a guess. `+1 415 555 0132` and `+44 20 7946 0958` are 11 and 12 digits; `020 7946 0958` is only meaningful if you know it is British.

```python
>>> from leadenrich import normalize_phone, InvalidValue
>>> normalize_phone("(415) 555-0132", region="US")
'+14155550132'
>>> normalize_phone("020 7946 0958", region="GB")
'+442079460958'
>>> normalize_phone("415 555 0132")
Traceback (most recent call last):
    ...
leadenrich.normalize.InvalidValue: phone '415 555 0132' has no country code and no region was supplied
```

A refusal with a reason beats a silent mis-merge. Invalid values are never dropped without a record: `Record.issues` carries the field, the raw value and the reason into the merged output.

### 4. Last-write-wins

Two sources say Berlin and Munich. Most pipelines keep whichever was written last and move on. This one keeps Berlin (because `crm` outranks `ads`) **and** reports the disagreement:

```
city  'Berlin'  <- crm
CONFLICT city: ads='Munich' vs crm='Berlin'
```

---

## Matching rules, stated plainly

Scores are a weighted mean over the fields both records share:

| Field | Weight | Rule |
| --- | --- | --- |
| email | 0.45 | exact match is decisive (score floored at 0.95) |
| phone | 0.20 | exact match floors the score at 0.85; same last 9 digits scores 0.75 |
| domain | 0.15 | same registrable domain scores 0.80 |
| company | 0.12 | Jaro-Winkler / token-set similarity |
| name | 0.08 | Jaro-Winkler / token-set similarity |

Two guards make the numbers safe rather than merely plausible:

- **A shared role mailbox is not identity.** `sales@`, `info@`, `support@` and friends score 0.70, not 1.0, because they say two records concern the same *organisation*.
- **Weak fields never auto-merge.** Without an email, phone or domain in common, the score is capped at 0.79, below the default threshold of 0.82. Two "John Smith"s at similar companies land in review instead of becoming one person.

Name and company use the **maximum** of Jaro-Winkler and token-set Dice: one handles typos (`Jon` / `Jonathan`), the other handles reordering (`Smith John` / `John Smith`). Averaging would let a bad measure drag down a confident one.

### Blocking keeps it linear-ish

Comparing every record to every other is fine for 500 leads and impossible for 5 million. Records are bucketed on keys chosen so a typo in a first name cannot prevent a match:

```
e:<email>   d:<domain>   p:<phone last 9>   s:<surname>   c:<first company token>
```

Only pairs sharing a bucket are compared. This is a recall trade-off, and the README says so rather than hiding it.

### Transitive merges

A matches B on email, B matches C on phone, so A, B and C are one entity. Union-find handles the group; per-field merges then run once per cluster rather than pairwise, so order cannot change the result.

---

## Confidence, explicitly

```
completeness = core fields present / 5          # email, phone, domain, company, name
pair         = mean comparison score in cluster  (singletons skip this)
penalty      = min(0.20, 0.05 x conflicts)
confidence   = 0.55 * pair + 0.45 * completeness - penalty      # clusters
confidence   = completeness                                      # singletons
```

Anything below `review_below` (default 0.70) is flagged `needs_review`. A merge can succeed and still be flagged: in the demo, Sam and Samuel merge on an exact phone number but their emails and name spellings disagree, so the entity is merged at 0.85 and routed to review at 0.64.

---

## Usage

### Merge files

```console
$ leadenrich merge crm.json ads.json --out merged.json --source-order crm,ads --region US
```

Input is a JSON array, a JSON object with `records`, or JSONL. Each record may carry its own `source`; otherwise the filename is used.

```json
{
  "fields": {"email": "jane.doe@acme.com", "city": "Berlin", "campaign": "spring-retargeting"},
  "provenance": {"email": "crm", "city": "crm", "campaign": "ads"},
  "conflicts": [{"field": "city", "values": [{"source": "ads", "value": "Munich"}, {"source": "crm", "value": "Berlin"}]}],
  "confidence": 0.86,
  "size": 2,
  "sources": ["ads", "crm"],
  "needs_review": false
}
```

### Inspect a decision

```console
$ leadenrich match examples/crm.json examples/ads.json --threshold 0.5 --region US
1.000  ads:588ddb03a4c0  <->  crm:fc98c0c5f917
        email    1.00  exact email
        company  1.00  company name similarity
        name     1.00  person name similarity
0.850  ads:4c637926d4a3  <->  crm:428bb9a60704
        email    0.10  same email domain, different mailbox
        phone    1.00  exact phone
        name     0.95  person name similarity
0.792  ads:15e24d26562c  <->  crm:705e58c8a409
        email    0.70  shared role mailbox
        company  1.00  company name similarity
        name     1.00  person name similarity
```

That last pair is the interesting one: an identical company name and an identical shared `info@` mailbox score 0.792, below the 0.82 threshold, so two records that a naive matcher would happily collapse stay separate.

### Find the bad input before it becomes a bad merge

```console
$ cat leads.json
[{"email": "a@acme.com", "phone": "nonsense"}]

$ leadenrich validate leads.json
leads:48ceae4bd30d  {'email': 'a@acme.com'}
    ! phone: no digits in phone: 'nonsense'  (raw='nonsense')

1 records, 1 field issues
```

The record survives; only the unusable field is dropped, and it is reported with its raw value so the upstream owner can fix it.

### Normalise one record

```console
$ leadenrich normalise "email=J.Doe@Gmail.com" "phone=020 7946 0958" --region GB
{
  "fields": {"email": "jdoe@gmail.com", "phone": "+442079460958"},
  "issues": []
}
```

### From Python

```python
from leadenrich import Record, resolve

records = [
    Record.build({"email": "jane@acme.com", "city": "Berlin"}, "crm"),
    Record.build({"email": "JANE@acme.com", "city": "Munich"}, "ads"),
]
entity = resolve(records, source_order=["crm", "ads"])[0]
print(entity.fields["city"])        # Berlin
print(entity.conflicts[0].field)    # city
print(entity.provenance["city"])    # crm
```

---

## Determinism

Same input, same output, byte for byte: records are ordered by `(source, id)` before any comparison, buckets are walked in sorted order, and every tie breaks lexicographically. Run the suite twice on reversed input and the merged JSON is identical, which is asserted in `test_resolution_is_deterministic`.

---

## Architecture

```
leadenrich/
  normalize.py   Provider-aware emails, E.164 phones, domains, IDN, names, companies
  similarity.py  Jaro-Winkler (4-char prefix cap) and token-set Dice
  resolve.py     Blocking, weighted comparison, union-find clustering, merges, confidence
  cli.py         leadenrich merge | match | validate | normalise
```

`normalize` knows nothing about matching. `similarity` knows nothing about records. `resolve` is the only module that knows what a merge costs.

---

## What it does not do

- **No network calls.** No Clearbit, no Apollo, no email verification service. This is the resolution layer; enrichment APIs plug in as another source file.
- **No real Public Suffix List.** `registrable_domain` is a heuristic covering the common `co.uk` case.
- **A short dialling-code table.** Sixteen regions with national-number lengths. Unknown regions raise rather than guess, which is the point.
- **No persistent identity graph.** Stateless per run. Feed it the same records and you get the same ids, which is what an external store needs to key on.
- **No merging of free-text addresses.** Street abbreviations (`St` / `Street`) need a postal normaliser, and half-doing it creates false negatives that look like real ones.

---

## Tests

```bash
python -m unittest discover -s tests -v
```

62 tests: Jaro-Winkler against published values (`MARTHA` / `MARHTA` = 0.961111, `DIXON` / `DICKSONX` = 0.813333), Gmail canonicalisation, E.164 handling for national and international input, IDN punycoding, legal-suffix stripping, role-mailbox scoring, the weak-field cap, transitive clustering, conflict detection, source precedence, and byte-identical output on reversed input.

---

## License

MIT (c) 2026 aiplusplus360
