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

    def test_rejects_read_only_path_without_usb_io(self) -> None:
        with self.assertRaisesRegex(DatastorePermissionError, "read-only"):
            validate_datastore_write("/datastore/uid", '{"value":"changed"}')

    def test_validates_numeric_range(self) -> None:
        validate_datastore_write("/datastore/mix/chan/0/matrix/fader", '{"value":4}')
        with self.assertRaisesRegex(DatastoreValidationError, "<= 4"):
            validate_datastore_write("/datastore/mix/chan/0/matrix/fader", '{"value":4.1}')

    def test_validates_type(self) -> None:
        with self.assertRaisesRegex(DatastoreValidationError, "integer"):
            validate_datastore_write("/datastore/mix/chan/0/hpf/freq", '{"value":440.5}')

    def test_validates_enum_membership(self) -> None:
        validate_datastore_write("/datastore/mix/chan/0/eq/highshelf/mode", '{"value":1}')
        with self.assertRaisesRegex(DatastoreValidationError, "one of"):
            validate_datastore_write("/datastore/mix/chan/0/eq/highshelf/mode", '{"value":2}')

    def test_validates_patch_relative_subpaths(self) -> None:
        validate_datastore_write("/datastore/mix/chan/0", '{"matrix/fader":2.0,"matrix/pan":0.2}')
        with self.assertRaisesRegex(DatastoreValidationError, ">= -1"):
            validate_datastore_write("/datastore/mix/chan/0", '{"matrix/pan":-2}')

    def test_forwards_unknown_paths_with_optional_warning(self) -> None:
        warnings: list[str] = []
        validate_datastore_write(
            "/datastore/future/path",
            '{"value":{"anything":true}}',
            warn_unknown=warnings.append,
        )
        self.assertEqual(warnings, ["/datastore/future/path"])
