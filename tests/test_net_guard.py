# -*- coding: utf-8 -*-
"""utils/net_guard.py 的单元测试。

对应 openspec/changes/harden-file-paths-and-cors/tasks.md 第 1.2 项。
"""

import unittest

from utils.net_guard import (
    NetGuardError,
    host_allowed,
    parse_allowed_hosts,
    parse_allowed_origins,
    validate_notify_url,
)


class ParseAllowedOriginsTestCase(unittest.TestCase):
    def test_ignores_blank_entries_and_whitespace(self):
        self.assertEqual(
            parse_allowed_origins('  https://a.example ,, https://b.example ,  '),
            ('https://a.example', 'https://b.example'),
        )

    def test_none_and_empty_yield_empty_tuple(self):
        for value in (None, '', '   ', ',,,'):
            self.assertEqual(parse_allowed_origins(value), ())

    def test_normalizes_scheme_and_host_case(self):
        self.assertEqual(parse_allowed_origins('HTTPS://A.Example'),
                         ('https://a.example',))

    def test_strips_trailing_slash(self):
        self.assertEqual(parse_allowed_origins('https://a.example/'),
                         ('https://a.example',))

    def test_preserves_port(self):
        self.assertEqual(parse_allowed_origins('http://a.example:8080'),
                         ('http://a.example:8080',))

    def test_deduplicates_and_keeps_order(self):
        self.assertEqual(
            parse_allowed_origins('https://b.example,https://a.example,https://b.example'),
            ('https://b.example', 'https://a.example'),
        )

    def test_wildcard_is_rejected(self):
        """通配来源配合凭据正是要消除的问题，必须响亮拒绝。"""
        with self.assertRaises(NetGuardError):
            parse_allowed_origins('*')
        with self.assertRaises(NetGuardError):
            parse_allowed_origins('https://a.example,*')

    def test_invalid_port_is_rejected(self):
        with self.assertRaises(NetGuardError):
            parse_allowed_origins('https://a.example:99999')


class ParseAllowedHostsTestCase(unittest.TestCase):
    def test_parses_plain_hosts_and_host_port_entries(self):
        self.assertEqual(
            parse_allowed_hosts(' Hook.Example , 127.0.0.1:9000 ,cb.example:8080'),
            ('hook.example', '127.0.0.1:9000', 'cb.example:8080'),
        )

    def test_none_and_empty_yield_empty_tuple(self):
        for value in (None, '', '  ', ',,'):
            self.assertEqual(parse_allowed_hosts(value), ())

    def test_deduplicates(self):
        self.assertEqual(parse_allowed_hosts('a.example,A.EXAMPLE'),
                         ('a.example',))

    def test_wildcard_is_rejected(self):
        with self.assertRaises(NetGuardError):
            parse_allowed_hosts('*')


class HostAllowedTestCase(unittest.TestCase):
    def test_empty_allowlist_denies_everything(self):
        self.assertFalse(host_allowed('a.example', 80, ()))
        self.assertFalse(host_allowed('a.example', 80, None))

    def test_plain_entry_matches_any_port(self):
        allowed = parse_allowed_hosts('a.example')
        for port in (80, 443, 8080):
            self.assertTrue(host_allowed('a.example', port, allowed))

    def test_host_port_entry_requires_matching_port(self):
        allowed = parse_allowed_hosts('a.example:9000')
        self.assertTrue(host_allowed('a.example', 9000, allowed))
        self.assertFalse(host_allowed('a.example', 80, allowed))
        self.assertFalse(host_allowed('a.example', 9001, allowed))

    def test_host_comparison_is_case_insensitive(self):
        allowed = parse_allowed_hosts('A.Example')
        self.assertTrue(host_allowed('a.EXAMPLE', 80, allowed))

    def test_host_port_entry_also_matches_exact_entry_list(self):
        allowed = parse_allowed_hosts('a.example, b.example:8443')
        self.assertTrue(host_allowed('A.example', 1234, allowed))
        self.assertTrue(host_allowed('b.example', 8443, allowed))
        self.assertFalse(host_allowed('b.example', 443, allowed))

    def test_unlisted_host_is_denied(self):
        allowed = parse_allowed_hosts('a.example')
        self.assertFalse(host_allowed('evil.example', 80, allowed))
        self.assertFalse(host_allowed('', 80, allowed))


class ValidateNotifyUrlTestCase(unittest.TestCase):
    def test_empty_url_is_allowed_without_allowlist(self):
        for value in (None, '', '   '):
            self.assertIsNone(validate_notify_url(value, ()))

    def test_empty_allowlist_rejects_any_url(self):
        with self.assertRaises(NetGuardError):
            validate_notify_url('http://a.example/hook', ())

    def test_registered_host_is_accepted(self):
        allowed = parse_allowed_hosts('a.example')
        self.assertEqual(validate_notify_url('http://a.example/hook', allowed),
                         'http://a.example/hook')

    def test_port_is_taken_from_url_or_scheme_default(self):
        only_8080 = parse_allowed_hosts('a.example:8080')
        self.assertEqual(
            validate_notify_url('http://a.example:8080/hook', only_8080),
            'http://a.example:8080/hook')
        with self.assertRaises(NetGuardError):
            validate_notify_url('http://a.example/hook', only_8080)

    def test_https_default_port_is_443(self):
        allowed = parse_allowed_hosts('a.example:443')
        self.assertEqual(
            validate_notify_url('https://a.example/hook', allowed),
            'https://a.example/hook')
        with self.assertRaises(NetGuardError):
            validate_notify_url('https://a.example:8443/hook', allowed)

    def test_unregistered_host_is_rejected(self):
        allowed = parse_allowed_hosts('a.example')
        with self.assertRaises(NetGuardError):
            validate_notify_url('http://169.254.169.254/latest/meta-data', allowed)

    def test_link_local_is_rejected_even_without_allowlist(self):
        with self.assertRaises(NetGuardError):
            validate_notify_url('http://169.254.169.254/latest/meta-data', ())

    def test_non_http_schemes_are_rejected(self):
        allowed = parse_allowed_hosts('a.example')
        for url in ('file:///etc/passwd', 'ftp://a.example/x',
                    'gopher://a.example/x', '//a.example/x', 'a.example/x'):
            with self.assertRaises(NetGuardError, msg=url):
                validate_notify_url(url, allowed)

    def test_url_without_host_is_rejected(self):
        allowed = parse_allowed_hosts('a.example')
        for url in ('http://', 'http:///path', 'https://'):
            with self.assertRaises(NetGuardError, msg=url):
                validate_notify_url(url, allowed)

    def test_invalid_port_is_rejected(self):
        allowed = parse_allowed_hosts('a.example')
        with self.assertRaises(NetGuardError):
            validate_notify_url('http://a.example:99999/hook', allowed)

    def test_host_case_is_insensitive(self):
        allowed = parse_allowed_hosts('a.example')
        self.assertIsNotNone(validate_notify_url('http://A.Example/hook', allowed))

    def test_userinfo_does_not_confuse_host_matching(self):
        allowed = parse_allowed_hosts('a.example')
        self.assertIsNotNone(
            validate_notify_url('http://user:pw@a.example/hook', allowed))
        with self.assertRaises(NetGuardError):
            validate_notify_url('http://a.example@evil.example/hook', allowed)


if __name__ == '__main__':
    unittest.main()
