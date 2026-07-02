from unittest import TestCase

from motu_proxy.http_server import (
    CrossOriginWrite,
    WritesDisabled,
    dispatch_datastore_request,
)
from motu_proxy.parser import DatastorePayload


class MotuHttpCompatibilityContractTests(TestCase):
    def test_get_contract_normalizes_datastore_paths_and_exposes_etag(self) -> None:
        calls: list[tuple[str, str | None, str | None, tuple[tuple[str, str], ...]]] = []

        def get(
            path: str,
            client: str | None = None,
            if_none_match: str | None = None,
            query_fields: tuple[tuple[str, str], ...] = (),
        ) -> DatastorePayload:
            calls.append((path, client, if_none_match, query_fields))
            return DatastorePayload(
                b'{"value":"0001f2fffe00c719"}',
                etag="5678",
                content_type="application/json",
            )

        result = dispatch_datastore_request(
            "GET",
            "/uid?client=1479701624",
            "",
            "",
            False,
            get,
            lambda path, body, client=None: b"{}",
        )

        self.assertEqual(result.status, 200)
        self.assertEqual(result.path, "/datastore/uid")
        self.assertEqual(result.response, b'{"value":"0001f2fffe00c719"}')
        self.assertEqual(result.etag, "5678")
        self.assertEqual(result.content_type, "application/json")
        self.assertEqual(
            calls,
            [("/datastore/uid", "1479701624", None, ())],
        )

    def test_long_poll_contract_maps_no_change_to_304(self) -> None:
        calls: list[tuple[str, str | None, str | None, tuple[tuple[str, str], ...]]] = []

        def get(
            path: str,
            client: str | None = None,
            if_none_match: str | None = None,
            query_fields: tuple[tuple[str, str], ...] = (),
        ) -> DatastorePayload:
            calls.append((path, client, if_none_match, query_fields))
            return DatastorePayload(b"", etag=if_none_match, status=304)

        result = dispatch_datastore_request(
            "GET",
            "/datastore",
            "",
            "",
            False,
            get,
            lambda path, body, client=None: b"{}",
            if_none_match="5678",
        )

        self.assertEqual(result.status, 304)
        self.assertEqual(result.path, "/datastore")
        self.assertEqual(result.response, b"")
        self.assertEqual(result.etag, "5678")
        self.assertEqual(calls, [("/datastore", None, "5678", ())])

    def test_post_contract_accepts_raw_json_and_form_json_bodies(self) -> None:
        cases = [
            ("application/json", '{"value":"linux"}'),
            ("application/x-www-form-urlencoded", 'json={"value":"linux"}'),
        ]
        for content_type, body in cases:
            with self.subTest(content_type=content_type):
                calls: list[tuple[str, bytes, str | None]] = []

                def make_post(target: list[tuple[str, bytes, str | None]]):
                    def post(path: str, request_body: bytes, client: str | None = None) -> bytes:
                        target.append((path, request_body, client))
                        return b'{"ok":true}'

                    return post

                result = dispatch_datastore_request(
                    "POST",
                    "/host/os?client=1479701624",
                    body,
                    content_type,
                    True,
                    lambda path, client=None: b"{}",
                    make_post(calls),
                    host="127.0.0.1:1280",
                )

                self.assertEqual(result.status, 200)
                self.assertEqual(result.path, "/datastore/host/os")
                self.assertEqual(result.response, b'{"ok":true}')
                self.assertEqual(
                    calls,
                    [("/datastore/host/os", b'{"value":"linux"}', "1479701624")],
                )

    def test_patch_contract_is_datastore_post_alias(self) -> None:
        calls: list[tuple[str, bytes, str | None]] = []

        def post(path: str, body: bytes, client: str | None = None) -> bytes:
            calls.append((path, body, client))
            return b'{"ok":true}'

        result = dispatch_datastore_request(
            "PATCH",
            "/host/os",
            '{"value":"linux"}',
            "application/json",
            True,
            lambda path, client=None: b"{}",
            post,
            host="127.0.0.1:1280",
        )

        self.assertEqual(result.status, 200)
        self.assertEqual(calls, [("/datastore/host/os", b'{"value":"linux"}', None)])

    def test_proxy_specific_write_safety_does_not_change_get_contract(self) -> None:
        result = dispatch_datastore_request(
            "GET",
            "/uid",
            "",
            "",
            False,
            lambda path, client=None: b'{"value":"0001f2fffe00c719"}',
            lambda path, body, client=None: b"{}",
            origin="http://not-the-proxy.example",
            host="127.0.0.1:1280",
        )
        self.assertEqual(result.response, b'{"value":"0001f2fffe00c719"}')

        with self.assertRaises(WritesDisabled):
            dispatch_datastore_request(
                "POST",
                "/host/os",
                '{"value":"linux"}',
                "application/json",
                False,
                lambda path, client=None: b"{}",
                lambda path, body, client=None: b"{}",
                host="127.0.0.1:1280",
            )

        with self.assertRaises(CrossOriginWrite):
            dispatch_datastore_request(
                "POST",
                "/host/os",
                '{"value":"linux"}',
                "application/json",
                True,
                lambda path, client=None: b"{}",
                lambda path, body, client=None: b"{}",
                origin="http://not-the-proxy.example",
                host="127.0.0.1:1280",
            )
