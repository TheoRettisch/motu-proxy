from unittest import TestCase

from motu_proxy.schema import (
    DatastorePermissionError,
    DatastoreValidationError,
    find_path_schema,
    validate_datastore_write,
)


class DatastoreSchemaTests(TestCase):
    def test_matches_exact_and_numeric_placeholder_paths(self) -> None:
        uid = find_path_schema("/datastore/uid")
        self.assertIsNotNone(uid)
        assert uid is not None
        self.assertEqual(uid.permission, "r")

        fader = find_path_schema("/datastore/mix/chan/12/matrix/fader")
        self.assertIsNotNone(fader)
        assert fader is not None
        self.assertEqual(fader.path, "mix/chan/<index>/matrix/fader")
        self.assertEqual(fader.minimum, 0)
        self.assertEqual(fader.maximum, 4)

    def test_matches_embedded_segment_placeholder_paths(self) -> None:
        input_stream = find_path_schema(
            "/datastore/avb/0001f2fffe00c719/cfg/0/input_streams/0/object_name"
        )
        self.assertIsNotNone(input_stream)
        assert input_stream is not None
        self.assertEqual(
            input_stream.path,
            "avb/<uid>/cfg/<index>/<input_or_output>_streams/<index>/object_name",
        )

        output_stream = find_path_schema(
            "/datastore/avb/0001f2fffe00c719/cfg/0/output_streams/0/num_ch"
        )
        self.assertIsNotNone(output_stream)
        assert output_stream is not None
        self.assertEqual(
            output_stream.path,
            "avb/<uid>/cfg/<index>/<input_or_output>_streams/<index>/num_ch",
        )

        self.assertIsNone(
            find_path_schema(
                "/datastore/avb/0001f2fffe00c719/cfg/0/side_streams/0/num_ch"
            )
        )

    def test_validates_avb_writable_paths(self) -> None:
        validate_datastore_write(
            "/datastore/avb/0001f2fffe00c719/entity_name", '{"value":"My 624"}'
        )
        validate_datastore_write(
            "/datastore/avb/0001f2fffe00c719/cfg/0/identify", '{"value":1}'
        )
        validate_datastore_write(
            "/datastore/avb/0001f2fffe00c719/cfg/0/input_streams/0/talker",
            '{"value":"0001f2fffe00c719:0"}',
        )

        with self.assertRaisesRegex(DatastoreValidationError, "0 or 1"):
            validate_datastore_write(
                "/datastore/avb/0001f2fffe00c719/cfg/0/identify",
                '{"value":2}',
            )

    def test_rejects_read_only_avb_paths_without_usb_io(self) -> None:
        with self.assertRaisesRegex(DatastorePermissionError, "read-only"):
            validate_datastore_write(
                "/datastore/avb/devs", '{"value":"0001f2fffe00c719"}'
            )

    def test_rejects_read_only_path_without_usb_io(self) -> None:
        with self.assertRaisesRegex(DatastorePermissionError, "read-only"):
            validate_datastore_write("/datastore/uid", '{"value":"changed"}')

    def test_validates_numeric_range(self) -> None:
        validate_datastore_write("/datastore/mix/chan/0/matrix/fader", '{"value":4}')
        with self.assertRaisesRegex(DatastoreValidationError, "<= 4"):
            validate_datastore_write(
                "/datastore/mix/chan/0/matrix/fader", '{"value":4.1}'
            )

    def test_rejects_non_finite_json_numbers(self) -> None:
        for value in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(DatastoreValidationError, "valid JSON"):
                    validate_datastore_write(
                        "/datastore/mix/chan/0/matrix/fader",
                        f'{{"value":{value}}}',
                    )

    def test_validates_type(self) -> None:
        with self.assertRaisesRegex(DatastoreValidationError, "integer"):
            validate_datastore_write(
                "/datastore/mix/chan/0/hpf/freq", '{"value":440.5}'
            )

    def test_validates_boolean_numeric_range(self) -> None:
        validate_datastore_write("/datastore/ext/enableHostVolControls", '{"value":1}')
        validate_datastore_write("/datastore/mix/chan/0/hpf/enable", '{"value":1.0}')
        with self.assertRaisesRegex(DatastoreValidationError, "0 or 1"):
            validate_datastore_write(
                "/datastore/ext/enableHostVolControls", '{"value":2}'
            )
        with self.assertRaisesRegex(DatastoreValidationError, "0 or 1"):
            validate_datastore_write(
                "/datastore/mix/chan/0/hpf/enable", '{"value":0.5}'
            )

    def test_boolean_schema_rejects_json_bool_values(self) -> None:
        with self.assertRaisesRegex(DatastoreValidationError, "integer"):
            validate_datastore_write(
                "/datastore/ext/enableHostVolControls", '{"value":true}'
            )
        with self.assertRaisesRegex(DatastoreValidationError, "number"):
            validate_datastore_write(
                "/datastore/mix/chan/0/hpf/enable", '{"value":false}'
            )

    def test_validates_enum_membership(self) -> None:
        validate_datastore_write(
            "/datastore/mix/chan/0/eq/highshelf/mode", '{"value":1}'
        )
        with self.assertRaisesRegex(DatastoreValidationError, "one of"):
            validate_datastore_write(
                "/datastore/mix/chan/0/eq/highshelf/mode", '{"value":2}'
            )

    def test_validates_patch_relative_subpaths(self) -> None:
        validate_datastore_write(
            "/datastore/mix/chan/0", '{"matrix/fader":2.0,"matrix/pan":0.2}'
        )
        with self.assertRaisesRegex(DatastoreValidationError, ">= -1"):
            validate_datastore_write("/datastore/mix/chan/0", '{"matrix/pan":-2}')

    def test_rejects_unknown_paths_by_default(self) -> None:
        with self.assertRaisesRegex(DatastoreValidationError, "known writable schema"):
            validate_datastore_write(
                "/datastore/future/path", '{"value":{"anything":true}}'
            )

    def test_allows_unknown_paths_with_explicit_opt_in_and_warning(self) -> None:
        warnings: list[str] = []
        validate_datastore_write(
            "/datastore/future/path",
            '{"value":{"anything":true}}',
            warn_unknown=warnings.append,
            allow_unknown=True,
        )
        self.assertEqual(warnings, ["/datastore/future/path"])
