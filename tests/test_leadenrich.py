"""Tests for lead-enrich."""

from __future__ import annotations

import unittest

from leadenrich import (
    InvalidValue,
    Record,
    best_similarity,
    blocking_keys,
    compare,
    is_disposable_domain,
    is_role_address,
    jaro,
    jaro_winkler,
    match_pairs,
    normalize_company,
    normalize_domain,
    normalize_email,
    normalize_fields,
    normalize_name,
    normalize_phone,
    registrable_domain,
    resolve,
    token_set_ratio,
)


class EmailTests(unittest.TestCase):
    def test_lowercases_and_trims(self):
        self.assertEqual(normalize_email("  Jane.Doe@Acme.COM  "), "jane.doe@acme.com")

    def test_strips_angle_brackets_and_mailto(self):
        self.assertEqual(normalize_email("<j@acme.com>"), "j@acme.com")
        self.assertEqual(normalize_email("mailto:J@Acme.com"), "j@acme.com")

    def test_gmail_dots_are_not_significant(self):
        self.assertEqual(normalize_email("J.Doe@Gmail.com"), "jdoe@gmail.com")
        self.assertEqual(normalize_email("jdoe@gmail.com"), "jdoe@gmail.com")

    def test_gmail_subaddress_kept_by_default(self):
        self.assertEqual(normalize_email("j.doe+news@gmail.com"), "jdoe+news@gmail.com")

    def test_gmail_subaddress_can_be_stripped(self):
        self.assertEqual(
            normalize_email("j.doe+news@gmail.com", strip_subaddress=True), "jdoe@gmail.com"
        )

    def test_other_providers_keep_dots(self):
        self.assertEqual(normalize_email("first.last@outlook.com"), "first.last@outlook.com")

    def test_rejects_invalid(self):
        for bad in ("", "no-at-sign", "a@b", "a@@b.com", "a b@c.com", "@acme.com"):
            with self.assertRaises(InvalidValue, msg=bad):
                normalize_email(bad)

    def test_role_detection(self):
        self.assertTrue(is_role_address("info@acme.com"))
        self.assertTrue(is_role_address("no-reply@acme.com"))
        self.assertTrue(is_role_address("sales.eu@acme.com"))
        self.assertFalse(is_role_address("jane@acme.com"))


class PhoneTests(unittest.TestCase):
    def test_us_with_region(self):
        self.assertEqual(normalize_phone("(415) 555-0132", region="US"), "+14155550132")

    def test_us_with_leading_one(self):
        self.assertEqual(normalize_phone("1-415-555-0132", region="US"), "+14155550132")

    def test_explicit_country_code(self):
        self.assertEqual(normalize_phone("+44 20 7946 0958", region="US"), "+442079460958")

    def test_uk_national_leading_zero(self):
        self.assertEqual(normalize_phone("020 7946 0958", region="GB"), "+442079460958")

    def test_international_prefix_00(self):
        self.assertEqual(normalize_phone("0044 20 7946 0958"), "+442079460958")

    def test_missing_region_is_an_error(self):
        with self.assertRaises(InvalidValue):
            normalize_phone("415 555 0132")

    def test_wrong_national_length_is_an_error(self):
        with self.assertRaises(InvalidValue):
            normalize_phone("555-0132", region="US")

    def test_unsupported_region(self):
        with self.assertRaises(InvalidValue):
            normalize_phone("5550132", region="ZZ")

    def test_too_short_international(self):
        with self.assertRaises(InvalidValue):
            normalize_phone("+123")

    def test_no_digits(self):
        with self.assertRaises(InvalidValue):
            normalize_phone("phone", region="US")


class DomainTests(unittest.TestCase):
    def test_strips_scheme_www_port_path(self):
        for raw in (
            "https://www.Example.com:443/path?q=1",
            "http://example.com",
            "www.example.com.",
            "EXAMPLE.com",
            "example.com:8080",
        ):
            self.assertEqual(normalize_domain(raw), "example.com", raw)

    def test_extracts_from_url_with_credentials(self):
        self.assertEqual(normalize_domain("https://user:pw@example.com/x"), "example.com")

    def test_idn_is_punycoded(self):
        self.assertEqual(normalize_domain("b\u00fccher.de"), "xn--bcher-kva.de")

    def test_rejects_junk(self):
        for bad in ("", "localhost", "not a domain", "http://"):
            with self.assertRaises(InvalidValue, msg=bad):
                normalize_domain(bad)

    def test_registrable_domain(self):
        self.assertEqual(registrable_domain("a.b.example.com"), "example.com")
        self.assertEqual(registrable_domain("shop.example.co.uk"), "example.co.uk")
        self.assertEqual(registrable_domain("example.com"), "example.com")

    def test_disposable(self):
        self.assertTrue(is_disposable_domain("mailinator.com"))
        self.assertFalse(is_disposable_domain("acme.com"))


class NameAndCompanyTests(unittest.TestCase):
    def test_name_title_case(self):
        self.assertEqual(normalize_name("jane doe"), "Jane Doe")
        self.assertEqual(normalize_name("JANE  DOE"), "Jane Doe")

    def test_titles_and_suffixes_dropped(self):
        self.assertEqual(normalize_name("Dr. Jane Doe PhD"), "Jane Doe")
        self.assertEqual(normalize_name("Jane Doe, Jr."), "Jane Doe")

    def test_apostrophes_and_hyphens(self):
        self.assertEqual(normalize_name("o'brien"), "O'Brien")
        self.assertEqual(normalize_name("anne-marie"), "Anne-Marie")

    def test_unicode_preserved(self):
        self.assertEqual(normalize_name("jos\u00e9 \u00e1lvarez"), "Jos\u00e9 \u00c1lvarez")

    def test_company_legal_suffixes(self):
        self.assertEqual(normalize_company("Acme Analytics, Inc."), "Acme Analytics")
        self.assertEqual(normalize_company("Acme Analytics LLC"), "Acme Analytics")
        self.assertEqual(normalize_company("Acme GmbH"), "Acme")

    def test_company_ampersand(self):
        self.assertEqual(normalize_company("Smith & Wesson"), "Smith and Wesson")

    def test_company_only_suffix_rejected(self):
        with self.assertRaises(InvalidValue):
            normalize_company("Inc.")

    def test_normalize_fields_reports_issues(self):
        fields, issues = normalize_fields(
            {"email": "J@Acme.com", "phone": "415 555 0132", "name": "jane doe"}, region="US"
        )
        self.assertEqual(fields["email"], "j@acme.com")
        self.assertEqual(fields["phone"], "+14155550132")
        self.assertEqual(issues, [])

        fields, issues = normalize_fields({"email": "nope"})
        self.assertNotIn("email", fields)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].field, "email")

    def test_aliases(self):
        fields, _ = normalize_fields({"website": "https://www.acme.com", "full_name": "jane doe"})
        self.assertEqual(fields["domain"], "acme.com")
        self.assertEqual(fields["name"], "Jane Doe")

    def test_unknown_fields_pass_through(self):
        fields, issues = normalize_fields({"city": " Berlin ", "score": 12})
        self.assertEqual(fields["city"], "Berlin")
        self.assertEqual(fields["score"], "12")
        self.assertEqual(issues, [])


class SimilarityTests(unittest.TestCase):
    def test_jaro_known_values(self):
        self.assertAlmostEqual(jaro("MARTHA", "MARHTA"), 0.944444, places=5)
        self.assertAlmostEqual(jaro("DIXON", "DICKSONX"), 0.766667, places=5)
        self.assertAlmostEqual(jaro("ABC", "ABC"), 1.0)
        self.assertEqual(jaro("", "ABC"), 0.0)

    def test_jaro_winkler_known_values(self):
        self.assertAlmostEqual(jaro_winkler("MARTHA", "MARHTA"), 0.961111, places=5)
        self.assertAlmostEqual(jaro_winkler("DIXON", "DICKSONX"), 0.813333, places=5)

    def test_winkler_boost_only_above_threshold(self):
        self.assertEqual(
            jaro_winkler("abcdefgh", "zzzzzzzz", boost_threshold=0.99),
            jaro("abcdefgh", "zzzzzzzz"),
        )

    def test_token_set_ratio(self):
        self.assertEqual(token_set_ratio("john smith", "smith john"), 1.0)
        self.assertEqual(token_set_ratio("john smith", "jane doe"), 0.0)

    def test_best_similarity_handles_reordering_and_typos(self):
        self.assertEqual(best_similarity("john smith", "smith john"), 1.0)
        self.assertGreater(best_similarity("jon smith", "john smith"), 0.85)
        self.assertLess(best_similarity("john smith", "jane doe"), 0.6)


class CompareTests(unittest.TestCase):
    def test_exact_email_is_decisive(self):
        a = Record.build({"email": "jane@acme.com", "name": "Jane Doe", "company": "Acme Inc"}, "crm")
        b = Record.build({"email": "JANE@acme.com", "name": "J. Doe", "company": "Acme"}, "ads")
        result = compare(a, b)
        self.assertGreaterEqual(result.score, 0.95)

    def test_role_email_is_not_decisive(self):
        a = Record.build({"email": "sales@acme.com", "name": "Jane Doe"}, "crm")
        b = Record.build({"email": "sales@acme.com", "name": "Bob Lee"}, "ads")
        result = compare(a, b)
        self.assertLess(result.score, 0.82)

    def test_shared_domain_is_weak(self):
        a = Record.build({"email": "jane@acme.com"}, "crm")
        b = Record.build({"email": "bob@acme.com"}, "ads")
        self.assertLess(compare(a, b).score, 0.3)

    def test_phone_only_match(self):
        a = Record.build({"phone": "+14155550132"}, "crm", region="US")
        b = Record.build({"phone": "(415) 555-0132"}, "ads", region="US")
        self.assertGreaterEqual(compare(a, b).score, 0.85)

    def test_name_and_company_alone_never_auto_match(self):
        a = Record.build({"name": "John Smith", "company": "Acme Analytics"}, "crm")
        b = Record.build({"name": "John Smith", "company": "Acme Analytics"}, "ads")
        result = compare(a, b)
        self.assertLess(result.score, 0.82)
        self.assertGreater(result.score, 0.7)

    def test_domain_and_company_boost(self):
        a = Record.build({"domain": "acme.com", "company": "Acme Analytics", "name": "Jane Doe"}, "crm")
        b = Record.build({"domain": "www.acme.com", "company": "Acme Analytics Inc", "name": "J Doe"}, "ads")
        self.assertGreaterEqual(compare(a, b).score, 0.90)

    def test_contradicting_strong_fields_score_low(self):
        a = Record.build({"email": "a@one.com", "name": "Jane Doe"}, "crm")
        b = Record.build({"email": "b@two.com", "name": "Bob Lee"}, "ads")
        # Different personal emails and different names: only residual name
        # similarity remains, far below any sane threshold.
        self.assertLess(compare(a, b).score, 0.2)

    def test_record_id_is_deterministic(self):
        first = Record.build({"email": "j@acme.com"}, "crm")
        second = Record.build({"email": "j@acme.com"}, "crm")
        self.assertEqual(first.id, second.id)
        self.assertEqual(len(first.id), 12)

    def test_record_id_depends_on_source(self):
        a = Record.build({"email": "j@acme.com"}, "crm")
        b = Record.build({"email": "j@acme.com"}, "ads")
        self.assertNotEqual(a.id, b.id)


class ResolveTests(unittest.TestCase):
    def test_merges_across_sources(self):
        records = [
            Record.build({"email": "jane@acme.com", "name": "Jane Doe", "phone": "4155550132"}, "crm", region="US"),
            Record.build({"email": "jane@acme.com", "name": "Jane Doe", "city": "Berlin"}, "ads"),
            Record.build({"email": "bob@other.com", "name": "Bob Lee"}, "crm"),
        ]
        merged = resolve(records)
        self.assertEqual(len(merged), 2)
        top = merged[0]
        self.assertEqual(top.size, 2)
        self.assertEqual(top.fields["city"], "Berlin")
        self.assertEqual(top.fields["phone"], "+14155550132")
        self.assertEqual(sorted(top.sources), ["ads", "crm"])

    def test_conflicts_are_reported(self):
        records = [
            Record.build({"email": "jane@acme.com", "name": "Jane Doe", "city": "Berlin"}, "crm"),
            Record.build({"email": "jane@acme.com", "name": "Jane Doe", "city": "Munich"}, "ads"),
        ]
        merged = resolve(records)[0]
        self.assertEqual(len(merged.conflicts), 1)
        conflict = merged.conflicts[0]
        self.assertEqual(conflict.field, "city")
        self.assertEqual(
            sorted(value for _, value in conflict.values), ["Berlin", "Munich"]
        )

    def test_source_order_decides_winner(self):
        records = [
            Record.build({"email": "jane@acme.com", "city": "Berlin"}, "ads"),
            Record.build({"email": "jane@acme.com", "city": "Munich"}, "crm"),
        ]
        merged = resolve(records, source_order=["crm", "ads"])[0]
        self.assertEqual(merged.fields["city"], "Munich")
        self.assertEqual(merged.provenance["city"], "crm")

    def test_frequency_decides_without_order(self):
        records = [
            Record.build({"email": "jane@acme.com", "city": "Berlin"}, "a"),
            Record.build({"email": "jane@acme.com", "city": "Berlin"}, "b"),
            Record.build({"email": "jane@acme.com", "city": "Munich"}, "c"),
        ]
        merged = resolve(records)[0]
        self.assertEqual(merged.fields["city"], "Berlin")

    def test_resolution_is_deterministic(self):
        records = [
            Record.build({"email": "jane@acme.com", "name": "Jane Doe"}, "crm"),
            Record.build({"email": "jane@acme.com", "name": "Jane Doe"}, "ads"),
            Record.build({"email": "bob@acme.com", "name": "Bob Lee", "company": "Acme"}, "ads"),
        ]
        first = [m.to_dict() for m in resolve(records)]
        second = [m.to_dict() for m in resolve(list(reversed(records)))]
        self.assertEqual(first, second)

    def test_transitive_merge(self):
        records = [
            Record.build({"email": "jane@acme.com"}, "a"),
            Record.build({"email": "jane@acme.com", "phone": "+14155550132"}, "b", region="US"),
            Record.build({"phone": "+14155550132"}, "c", region="US"),
        ]
        merged = resolve(records)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].size, 3)

    def test_unrelated_records_stay_separate(self):
        records = [
            Record.build({"email": "jane@acme.com", "name": "Jane Doe"}, "crm"),
            Record.build({"email": "john@zenith.com", "name": "John Smith"}, "crm"),
        ]
        merged = resolve(records)
        self.assertEqual(len(merged), 2)
        self.assertTrue(all(m.size == 1 for m in merged))

    def test_singleton_confidence_is_completeness(self):
        full = Record.build(
            {"email": "j@acme.com", "phone": "+14155550132", "domain": "acme.com",
             "company": "Acme", "name": "Jane Doe"},
            "crm", region="US",
        )
        self.assertEqual(resolve([full])[0].confidence, 1.0)
        partial = Record.build({"email": "j@acme.com"}, "crm")
        self.assertAlmostEqual(resolve([partial])[0].confidence, 0.2, places=2)

    def test_low_confidence_flagged_for_review(self):
        records = [
            Record.build({"name": "John Smith", "company": "Acme Analytics"}, "a"),
            Record.build({"name": "John Smith", "company": "Acme Analytics"}, "b"),
        ]
        merged = resolve(records, threshold=0.75)
        self.assertEqual(len(merged), 1)
        self.assertTrue(merged[0].needs_review)

    def test_conflicts_lower_confidence(self):
        agreeing = [
            Record.build({"email": "j@acme.com", "city": "Berlin"}, "a"),
            Record.build({"email": "j@acme.com", "city": "Berlin"}, "b"),
        ]
        conflicting = [
            Record.build({"email": "j@acme.com", "city": "Berlin"}, "a"),
            Record.build({"email": "j@acme.com", "city": "Munich"}, "b"),
        ]
        self.assertGreater(resolve(agreeing)[0].confidence, resolve(conflicting)[0].confidence)

    def test_empty_input(self):
        self.assertEqual(resolve([]), [])

    def test_issues_are_carried_into_merge(self):
        records = [
            Record.build({"email": "j@acme.com", "phone": "nonsense"}, "a", region="US"),
        ]
        merged = resolve(records)[0]
        self.assertEqual(len(merged.issues), 1)
        self.assertEqual(merged.issues[0]["field"], "phone")
        self.assertEqual(merged.issues[0]["source"], "a")

    def test_match_pairs_sorted_by_score(self):
        records = [
            Record.build({"email": "j@acme.com", "phone": "+14155550132"}, "a", region="US"),
            Record.build({"email": "j@acme.com"}, "b"),
            Record.build({"email": "j@acme.com", "phone": "(415) 555-0132"}, "c", region="US"),
        ]
        pairs = match_pairs(records, threshold=0.8)
        self.assertGreaterEqual(len(pairs), 2)
        scores = [p.score for p in pairs]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_blocking_keys(self):
        record = Record.build(
            {"email": "j@acme.com", "domain": "acme.com", "name": "Jane Doe", "company": "Acme Analytics"},
            "a",
        )
        keys = blocking_keys(record)
        self.assertIn("e:j@acme.com", keys)
        self.assertIn("d:acme.com", keys)
        self.assertIn("s:doe", keys)
        self.assertIn("c:acme", keys)


if __name__ == "__main__":
    unittest.main()
