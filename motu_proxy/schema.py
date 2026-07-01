"""Documented MOTU datastore schema and write validation."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from numbers import Real
from typing import Callable, Iterable

from .json_body import InvalidJsonBody, load_json_object
from .paths import normalize_path


DATASTORE_PREFIX = "/datastore/"
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
SEGMENT_PLACEHOLDER_RE = re.compile(r"<([A-Za-z0-9_]+)>")
UID_RE = re.compile(r"^[0-9a-fA-F]{16}$")
PLACEHOLDER_VALUES = {
    "ibank_or_obank": {"ibank", "obank"},
    "input_or_output": {"input", "output"},
}


class DatastorePermissionError(RuntimeError):
    pass


class DatastoreValidationError(RuntimeError):
    pass


@dataclass(frozen=True)
class PathSchema:
    path: str
    value_type: str
    permission: str
    minimum: int | float | None = None
    maximum: int | float | None = None
    enum_values: tuple[int | float | str, ...] | None = None

    @property
    def segments(self) -> tuple[str, ...]:
        return tuple(self.path.split("/"))


RAW_SCHEMA: tuple[
    tuple[str, str, str, int | float | None, int | float | None, str | None], ...
] = (
    ("uid", "string", "r", None, None, None),
    ("host/os", "string", "rw", None, None, None),
    ("ext/caps/avb", "semver_opt", "r", None, None, None),
    ("ext/caps/router", "semver_opt", "r", None, None, None),
    ("ext/caps/mixer", "semver_opt", "r", None, None, None),
    ("avb/devs", "string_list", "r", None, None, None),
    ("avb/<uid>/entity_model_id_h32", "int", "r", None, None, None),
    ("avb/<uid>/entity_model_id_l32", "int", "r", None, None, None),
    ("avb/<uid>/entity_name", "string", "rw", None, None, None),
    ("avb/<uid>/model_name", "string", "r", None, None, None),
    ("avb/<uid>/hostname", "string_opt", "r", None, None, None),
    ("avb/<uid>/master_clock/capable", "int_bool", "r", None, None, None),
    ("avb/<uid>/master_clock/uid", "string_opt", "rw", None, None, None),
    ("avb/<uid>/vendor_name", "string", "r", None, None, None),
    ("avb/<uid>/firmware_version", "string", "r", None, None, None),
    ("avb/<uid>/serial_number", "string", "r", None, None, None),
    ("avb/<uid>/controller_ignore", "int_bool", "r", None, None, None),
    ("avb/<uid>/acquired_id", "string", "r", None, None, None),
    ("avb/<uid>/motu.mdns.type", "string_opt", "r", None, None, None),
    ("avb/<uid>/apiversion", "semver_opt", "r", None, None, None),
    ("avb/<uid>/url", "string_opt", "r", None, None, None),
    ("avb/<uid>/current_configuration", "int", "rw", None, None, None),
    ("avb/<uid>/cfg/<index>/object_name", "string", "r", None, None, None),
    ("avb/<uid>/cfg/<index>/identify", "int_bool", "rw", None, None, None),
    ("avb/<uid>/cfg/<index>/current_sampling_rate", "int", "rw", None, None, None),
    ("avb/<uid>/cfg/<index>/sample_rates", "int_list", "r", None, None, None),
    ("avb/<uid>/cfg/<index>/clock_source_index", "int", "rw", None, None, None),
    ("avb/<uid>/cfg/<index>/clock_sources/num", "int", "r", None, None, None),
    (
        "avb/<uid>/cfg/<index>/clock_sources/<index>/object_name",
        "string",
        "r",
        None,
        None,
        None,
    ),
    (
        "avb/<uid>/cfg/<index>/clock_sources/<index>/type",
        "string",
        "r",
        None,
        None,
        None,
    ),
    (
        "avb/<uid>/cfg/<index>/clock_sources/<index>/stream_id",
        "int_opt",
        "r",
        None,
        None,
        None,
    ),
    (
        "avb/<uid>/cfg/<index>/<input_or_output>_streams/num",
        "int",
        "r",
        None,
        None,
        None,
    ),
    (
        "avb/<uid>/cfg/<index>/<input_or_output>_streams/<index>/object_name",
        "string",
        "r",
        None,
        None,
        None,
    ),
    (
        "avb/<uid>/cfg/<index>/<input_or_output>_streams/<index>/num_ch",
        "int",
        "r",
        None,
        None,
        None,
    ),
    (
        "avb/<uid>/cfg/<index>/input_streams/<index>/talker",
        "string_pair",
        "rw",
        None,
        None,
        None,
    ),
    ("ext/clockLocked", "int_bool", "r", None, None, None),
    ("ext/wordClockMode", "string", "rw", None, None, None),
    ("ext/wordClockThru", "string", "rw", None, None, None),
    ("ext/smuxPerBank", "int_bool", "r", None, None, None),
    ("ext/vlimit/lookahead", "int_bool_opt", "rw", None, None, None),
    ("ext/enableHostVolControls", "int_bool", "rw", None, None, None),
    ("ext/maxUSBToHost", "int", "rw", None, None, None),
    ("ext/<ibank_or_obank>/<index>/name", "string", "r", None, None, None),
    ("ext/<ibank_or_obank>/<index>/maxCh", "int", "r", None, None, None),
    ("ext/<ibank_or_obank>/<index>/numCh", "int", "r", None, None, None),
    ("ext/<ibank_or_obank>/<index>/userCh", "int", "rw", None, None, None),
    ("ext/<ibank_or_obank>/<index>/calcCh", "int", "r", None, None, None),
    ("ext/<ibank_or_obank>/<index>/smux", "string", "rw", None, None, None),
    ("ext/ibank/<index>/madiClock", "string", "r", None, None, None),
    ("ext/obank/<index>/madiClock", "string", "rw", None, None, None),
    ("ext/ibank/<index>/madiFormat", "int", "r", None, None, None),
    ("ext/obank/<index>/madiFormat", "int", "rw", None, None, None),
    ("ext/<ibank_or_obank>/<index>/ch/<index>/name", "string", "rw", None, None, None),
    ("ext/obank/<index>/ch/<index>/src", "int_pair_opt", "rw", None, None, None),
    (
        "ext/<ibank_or_obank>/<index>/ch/<index>/phase",
        "int_bool_opt",
        "rw",
        None,
        None,
        None,
    ),
    (
        "ext/<ibank_or_obank>/<index>/ch/<index>/pad",
        "int_bool_opt",
        "rw",
        None,
        None,
        None,
    ),
    ("ext/ibank/<index>/ch/<index>/48V", "int_bool_opt", "rw", None, None, None),
    ("ext/ibank/<index>/ch/<index>/vlLimit", "int_bool_opt", "rw", None, None, None),
    ("ext/ibank/<index>/ch/<index>/vlClip", "int_bool_opt", "rw", None, None, None),
    ("ext/<ibank_or_obank>/<index>/ch/<index>/trim", "int_opt", "rw", None, None, None),
    (
        "ext/<ibank_or_obank>/<index>/ch/<index>/trimRange",
        "int_pair_opt",
        "rw",
        None,
        None,
        None,
    ),
    (
        "ext/<ibank_or_obank>/<index>/ch/<index>/stereoTrim",
        "int_opt",
        "rw",
        None,
        None,
        None,
    ),
    (
        "ext/<ibank_or_obank>/<index>/ch/<index>/stereoTrimRange",
        "int_pair_opt",
        "rw",
        None,
        None,
        None,
    ),
    (
        "ext/<ibank_or_obank>/<index>/ch/<index>/connection",
        "int_bool_opt",
        "r",
        None,
        None,
        None,
    ),
    ("mix/ctrls/dsp/usage", "int", "r", None, None, None),
    ("mix/ctrls/<effect_resource>/avail", "int_bool_opt", "r", None, None, None),
    ("mix/chan/<index>/matrix/aux/<index>/send", "real", "rw", 0, 4, None),
    ("mix/chan/<index>/matrix/group/<index>/send", "real", "rw", 0, 4, None),
    ("mix/chan/<index>/matrix/reverb/<index>/send", "real", "rw", 0, 4, None),
    ("mix/chan/<index>/matrix/aux/<index>/pan", "real", "rw", -1, 1, None),
    ("mix/chan/<index>/matrix/group/<index>/pan", "real", "rw", -1, 1, None),
    ("mix/chan/<index>/matrix/reverb/<index>/pan", "real", "rw", -1, 1, None),
    ("mix/chan/<index>/hpf/enable", "real_bool", "rw", None, None, None),
    ("mix/chan/<index>/hpf/freq", "int", "rw", 20, 20000, None),
    ("mix/chan/<index>/eq/highshelf/enable", "real_bool", "rw", None, None, None),
    ("mix/chan/<index>/eq/highshelf/freq", "int", "rw", 20, 20000, None),
    ("mix/chan/<index>/eq/highshelf/gain", "real", "rw", -20, 20, None),
    ("mix/chan/<index>/eq/highshelf/bw", "real", "rw", 0.01, 3, None),
    (
        "mix/chan/<index>/eq/highshelf/mode",
        "real_enum",
        "rw",
        None,
        None,
        "Shelf=0,Para=1",
    ),
    ("mix/chan/<index>/eq/mid1/enable", "real_bool", "rw", None, None, None),
    ("mix/chan/<index>/eq/mid1/freq", "int", "rw", 20, 20000, None),
    ("mix/chan/<index>/eq/mid1/gain", "real", "rw", -20, 20, None),
    ("mix/chan/<index>/eq/mid1/bw", "real", "rw", 0.01, 3, None),
    ("mix/chan/<index>/eq/mid2/enable", "real_bool", "rw", None, None, None),
    ("mix/chan/<index>/eq/mid2/freq", "int", "rw", 20, 20000, None),
    ("mix/chan/<index>/eq/mid2/gain", "real", "rw", -20, 20, None),
    ("mix/chan/<index>/eq/mid2/bw", "real", "rw", 0.01, 3, None),
    ("mix/chan/<index>/eq/lowshelf/enable", "real_bool", "rw", None, None, None),
    ("mix/chan/<index>/eq/lowshelf/freq", "int", "rw", 20, 20000, None),
    ("mix/chan/<index>/eq/lowshelf/gain", "real", "rw", -20, 20, None),
    ("mix/chan/<index>/eq/lowshelf/bw", "real", "rw", 0.01, 3, None),
    (
        "mix/chan/<index>/eq/lowshelf/mode",
        "real_enum",
        "rw",
        None,
        None,
        "Shelf=0,Para=1",
    ),
    ("mix/chan/<index>/gate/enable", "real_bool", "rw", None, None, None),
    ("mix/chan/<index>/gate/release", "real", "rw", 50, 2000, None),
    ("mix/chan/<index>/gate/threshold", "real", "rw", 0, 1, None),
    ("mix/chan/<index>/gate/attack", "real", "rw", 10, 500, None),
    ("mix/chan/<index>/comp/enable", "real_bool", "rw", None, None, None),
    ("mix/chan/<index>/comp/release", "real", "rw", 10, 2000, None),
    ("mix/chan/<index>/comp/threshold", "real", "rw", -40, 0, None),
    ("mix/chan/<index>/comp/ratio", "real", "rw", 1, 10, None),
    ("mix/chan/<index>/comp/attack", "real", "rw", 10, 100, None),
    ("mix/chan/<index>/comp/trim", "real", "rw", -20, 20, None),
    ("mix/chan/<index>/comp/peak", "real_enum", "rw", None, None, "RMS=0,Peak=1"),
    ("mix/chan/<index>/matrix/enable", "real_bool", "rw", None, None, None),
    ("mix/chan/<index>/matrix/solo", "real_bool", "rw", None, None, None),
    ("mix/chan/<index>/matrix/mute", "real_bool", "rw", None, None, None),
    ("mix/chan/<index>/matrix/pan", "real", "rw", -1, 1, None),
    ("mix/chan/<index>/matrix/fader", "real", "rw", 0, 4, None),
    ("mix/main/<index>/eq/highshelf/enable", "real_bool", "rw", None, None, None),
    ("mix/main/<index>/eq/highshelf/freq", "int", "rw", 20, 20000, None),
    ("mix/main/<index>/eq/highshelf/gain", "real", "rw", -20, 20, None),
    ("mix/main/<index>/eq/highshelf/bw", "real", "rw", 0.01, 3, None),
    (
        "mix/main/<index>/eq/highshelf/mode",
        "real_enum",
        "rw",
        None,
        None,
        "Shelf=0,Para=1",
    ),
    ("mix/main/<index>/eq/mid1/enable", "real_bool", "rw", None, None, None),
    ("mix/main/<index>/eq/mid1/freq", "int", "rw", 20, 20000, None),
    ("mix/main/<index>/eq/mid1/gain", "real", "rw", -20, 20, None),
    ("mix/main/<index>/eq/mid1/bw", "real", "rw", 0.01, 3, None),
    ("mix/main/<index>/eq/mid2/enable", "real_bool", "rw", None, None, None),
    ("mix/main/<index>/eq/mid2/freq", "int", "rw", 20, 20000, None),
    ("mix/main/<index>/eq/mid2/gain", "real", "rw", -20, 20, None),
    ("mix/main/<index>/eq/mid2/bw", "real", "rw", 0.01, 3, None),
    ("mix/main/<index>/eq/lowshelf/enable", "real_bool", "rw", None, None, None),
    ("mix/main/<index>/eq/lowshelf/freq", "int", "rw", 20, 20000, None),
    ("mix/main/<index>/eq/lowshelf/gain", "real", "rw", -20, 20, None),
    ("mix/main/<index>/eq/lowshelf/bw", "real", "rw", 0.01, 3, None),
    (
        "mix/main/<index>/eq/lowshelf/mode",
        "real_enum",
        "rw",
        None,
        None,
        "Shelf=0,Para=1",
    ),
    ("mix/main/<index>/leveler/enable", "real_bool", "rw", None, None, None),
    ("mix/main/<index>/leveler/makeup", "real", "rw", 0, 100, None),
    ("mix/main/<index>/leveler/reduction", "real", "rw", 0, 100, None),
    ("mix/main/<index>/leveler/limit", "real_bool", "rw", None, None, None),
    ("mix/main/<index>/matrix/enable", "real_bool", "rw", None, None, None),
    ("mix/main/<index>/matrix/mute", "real_bool", "rw", None, None, None),
    ("mix/main/<index>/matrix/fader", "real", "rw", 0, 4, None),
    ("mix/aux/<index>/eq/highshelf/enable", "real_bool", "rw", None, None, None),
    ("mix/aux/<index>/eq/highshelf/freq", "int", "rw", 20, 20000, None),
    ("mix/aux/<index>/eq/highshelf/gain", "real", "rw", -20, 20, None),
    ("mix/aux/<index>/eq/highshelf/bw", "real", "rw", 0.01, 3, None),
    (
        "mix/aux/<index>/eq/highshelf/mode",
        "real_enum",
        "rw",
        None,
        None,
        "Shelf=0,Para=1",
    ),
    ("mix/aux/<index>/eq/mid1/enable", "real_bool", "rw", None, None, None),
    ("mix/aux/<index>/eq/mid1/freq", "int", "rw", 20, 20000, None),
    ("mix/aux/<index>/eq/mid1/gain", "real", "rw", -20, 20, None),
    ("mix/aux/<index>/eq/mid1/bw", "real", "rw", 0.01, 3, None),
    ("mix/aux/<index>/eq/mid2/enable", "real_bool", "rw", None, None, None),
    ("mix/aux/<index>/eq/mid2/freq", "int", "rw", 20, 20000, None),
    ("mix/aux/<index>/eq/mid2/gain", "real", "rw", -20, 20, None),
    ("mix/aux/<index>/eq/mid2/bw", "real", "rw", 0.01, 3, None),
    ("mix/aux/<index>/eq/lowshelf/enable", "real_bool", "rw", None, None, None),
    ("mix/aux/<index>/eq/lowshelf/freq", "int", "rw", 20, 20000, None),
    ("mix/aux/<index>/eq/lowshelf/gain", "real", "rw", -20, 20, None),
    ("mix/aux/<index>/eq/lowshelf/bw", "real", "rw", 0.01, 3, None),
    (
        "mix/aux/<index>/eq/lowshelf/mode",
        "real_enum",
        "rw",
        None,
        None,
        "Shelf=0,Para=1",
    ),
    ("mix/aux/<index>/matrix/enable", "real_bool", "rw", None, None, None),
    ("mix/aux/<index>/matrix/prefader", "real_bool", "rw", None, None, None),
    ("mix/aux/<index>/matrix/panner", "real_bool", "rw", None, None, None),
    ("mix/aux/<index>/matrix/mute", "real_bool", "rw", None, None, None),
    ("mix/aux/<index>/matrix/fader", "real", "rw", 0, 4, None),
    ("mix/group/<index>/matrix/aux/<index>/send", "real", "rw", 0, 4, None),
    ("mix/group/<index>/matrix/reverb/<index>/send", "real", "rw", 0, 4, None),
    ("mix/group/<index>/eq/highshelf/enable", "real_bool", "rw", None, None, None),
    ("mix/group/<index>/eq/highshelf/freq", "int", "rw", 20, 20000, None),
    ("mix/group/<index>/eq/highshelf/gain", "real", "rw", -20, 20, None),
    ("mix/group/<index>/eq/highshelf/bw", "real", "rw", 0.01, 3, None),
    (
        "mix/group/<index>/eq/highshelf/mode",
        "real_enum",
        "rw",
        None,
        None,
        "Shelf=0,Para=1",
    ),
    ("mix/group/<index>/eq/mid1/enable", "real_bool", "rw", None, None, None),
    ("mix/group/<index>/eq/mid1/freq", "int", "rw", 20, 20000, None),
    ("mix/group/<index>/eq/mid1/gain", "real", "rw", -20, 20, None),
    ("mix/group/<index>/eq/mid1/bw", "real", "rw", 0.01, 3, None),
    ("mix/group/<index>/eq/mid2/enable", "real_bool", "rw", None, None, None),
    ("mix/group/<index>/eq/mid2/freq", "int", "rw", 20, 20000, None),
    ("mix/group/<index>/eq/mid2/gain", "real", "rw", -20, 20, None),
    ("mix/group/<index>/eq/mid2/bw", "real", "rw", 0.01, 3, None),
    ("mix/group/<index>/eq/lowshelf/enable", "real_bool", "rw", None, None, None),
    ("mix/group/<index>/eq/lowshelf/freq", "int", "rw", 20, 20000, None),
    ("mix/group/<index>/eq/lowshelf/gain", "real", "rw", -20, 20, None),
    ("mix/group/<index>/eq/lowshelf/bw", "real", "rw", 0.01, 3, None),
    (
        "mix/group/<index>/eq/lowshelf/mode",
        "real_enum",
        "rw",
        None,
        None,
        "Shelf=0,Para=1",
    ),
    ("mix/group/<index>/leveler/enable", "real_bool", "rw", None, None, None),
    ("mix/group/<index>/leveler/makeup", "real", "rw", 0, 100, None),
    ("mix/group/<index>/leveler/reduction", "real", "rw", 0, 100, None),
    ("mix/group/<index>/leveler/limit", "real_bool", "rw", None, None, None),
    ("mix/group/<index>/matrix/enable", "real_bool", "rw", None, None, None),
    ("mix/group/<index>/matrix/solo", "real_bool", "rw", None, None, None),
    ("mix/group/<index>/matrix/prefader", "real_bool", "rw", None, None, None),
    ("mix/group/<index>/matrix/panner", "real_bool", "rw", None, None, None),
    ("mix/group/<index>/matrix/mute", "real_bool", "rw", None, None, None),
    ("mix/group/<index>/matrix/fader", "real", "rw", 0, 4, None),
    ("mix/reverb/<index>/matrix/aux/<index>/send", "real", "rw", 0, 4, None),
    ("mix/reverb/<index>/matrix/reverb/<index>/send", "real", "rw", 0, 4, None),
    ("mix/reverb/<index>/eq/highshelf/enable", "real_bool", "rw", None, None, None),
    ("mix/reverb/<index>/eq/highshelf/freq", "int", "rw", 20, 20000, None),
    ("mix/reverb/<index>/eq/highshelf/gain", "real", "rw", -20, 20, None),
    ("mix/reverb/<index>/eq/highshelf/bw", "real", "rw", 0.01, 3, None),
    (
        "mix/reverb/<index>/eq/highshelf/mode",
        "real_enum",
        "rw",
        None,
        None,
        "Shelf=0,Para=1",
    ),
    ("mix/reverb/<index>/eq/mid1/enable", "real_bool", "rw", None, None, None),
    ("mix/reverb/<index>/eq/mid1/freq", "int", "rw", 20, 20000, None),
    ("mix/reverb/<index>/eq/mid1/gain", "real", "rw", -20, 20, None),
    ("mix/reverb/<index>/eq/mid1/bw", "real", "rw", 0.01, 3, None),
    ("mix/reverb/<index>/eq/mid2/enable", "real_bool", "rw", None, None, None),
    ("mix/reverb/<index>/eq/mid2/freq", "int", "rw", 20, 20000, None),
    ("mix/reverb/<index>/eq/mid2/gain", "real", "rw", -20, 20, None),
    ("mix/reverb/<index>/eq/mid2/bw", "real", "rw", 0.01, 3, None),
    ("mix/reverb/<index>/eq/lowshelf/enable", "real_bool", "rw", None, None, None),
    ("mix/reverb/<index>/eq/lowshelf/freq", "int", "rw", 20, 20000, None),
    ("mix/reverb/<index>/eq/lowshelf/gain", "real", "rw", -20, 20, None),
    ("mix/reverb/<index>/eq/lowshelf/bw", "real", "rw", 0.01, 3, None),
    (
        "mix/reverb/<index>/eq/lowshelf/mode",
        "real_enum",
        "rw",
        None,
        None,
        "Shelf=0,Para=1",
    ),
    ("mix/reverb/<index>/leveler/enable", "real_bool", "rw", None, None, None),
    ("mix/reverb/<index>/leveler/makeup", "real", "rw", 0, 100, None),
    ("mix/reverb/<index>/leveler/reduction", "real", "rw", 0, 100, None),
    ("mix/reverb/<index>/leveler/limit", "real_bool", "rw", None, None, None),
    ("mix/reverb/<index>/matrix/enable", "real_bool", "rw", None, None, None),
    ("mix/reverb/<index>/matrix/solo", "real_bool", "rw", None, None, None),
    ("mix/reverb/<index>/matrix/prefader", "real_bool", "rw", None, None, None),
    ("mix/reverb/<index>/matrix/panner", "real_bool", "rw", None, None, None),
    ("mix/reverb/<index>/matrix/mute", "real_bool", "rw", None, None, None),
    ("mix/reverb/<index>/matrix/fader", "real", "rw", 0, 4, None),
    ("mix/reverb/<index>/reverb/enable", "real_bool", "rw", None, None, None),
    ("mix/reverb/<index>/reverb/reverbtime", "int", "rw", 100, 60000, None),
    ("mix/reverb/<index>/reverb/hf", "int", "rw", 500, 15000, None),
    ("mix/reverb/<index>/reverb/mf", "int", "rw", 500, 15000, None),
    ("mix/reverb/<index>/reverb/predelay", "int", "rw", 0, 500, None),
    ("mix/reverb/<index>/reverb/mfratio", "int", "rw", 1, 100, None),
    ("mix/reverb/<index>/reverb/hfratio", "int", "rw", 1, 100, None),
    ("mix/reverb/<index>/reverb/tailspread", "int", "rw", -100, 100, None),
    ("mix/reverb/<index>/reverb/mod", "int", "rw", 0, 100, None),
    ("mix/monitor/<index>/matrix/enable", "real_bool", "rw", None, None, None),
    ("mix/monitor/<index>/matrix/mute", "real_bool", "rw", None, None, None),
    ("mix/monitor/<index>/matrix/fader", "real", "rw", 0, 4, None),
    ("mix/monitor/<index>/assign", "int", "rw", -2, 4096, None),
    ("mix/monitor/<index>/override", "int", "rw", -1, 4096, None),
    ("mix/monitor/<index>/auto", "real_bool", "rw", None, None, None),
)


def _parse_enum(enum_values: str | None) -> tuple[int | float | str, ...] | None:
    if enum_values is None:
        return None
    values: list[int | float | str] = []
    for item in enum_values.split(","):
        _, _, raw_value = item.partition("=")
        value = raw_value.strip()
        try:
            values.append(int(value, 10))
            continue
        except ValueError:
            pass
        try:
            values.append(float(value))
            continue
        except ValueError:
            pass
        values.append(value)
    return tuple(values)


SCHEMA: tuple[PathSchema, ...] = tuple(
    PathSchema(path, value_type, permission, minimum, maximum, _parse_enum(enum_values))
    for path, value_type, permission, minimum, maximum, enum_values in RAW_SCHEMA
)


def find_path_schema(path: str) -> PathSchema | None:
    relative = _relative_datastore_path(path)
    if relative is None:
        return None
    requested = tuple(segment for segment in relative.split("/") if segment)
    best: tuple[tuple[int, int], PathSchema] | None = None
    for entry in SCHEMA:
        score = _match_score(entry.segments, requested)
        if score is None:
            continue
        candidate = (score, entry)
        if best is None or candidate[0] > best[0]:
            best = candidate
    return best[1] if best is not None else None


def validate_datastore_write(
    path: str,
    json_body: str,
    warn_unknown: Callable[[str], None] | None = None,
    allow_unknown: bool = False,
) -> None:
    try:
        body = load_json_object(json_body)
    except InvalidJsonBody as exc:
        raise DatastoreValidationError(str(exc)) from exc
    for target_path, value in _iter_write_values(path, body):
        entry = find_path_schema(target_path)
        if entry is None:
            if allow_unknown:
                if warn_unknown is not None:
                    warn_unknown(target_path)
                continue
            raise DatastoreValidationError(
                f"{target_path} is not in the known writable schema"
            )
        if entry.permission != "rw":
            raise DatastorePermissionError(f"{target_path} is read-only")
        _validate_value(target_path, entry, value)


def _relative_datastore_path(path: str) -> str | None:
    normalized = normalize_path(path)
    if normalized == "/datastore":
        return ""
    if not normalized.startswith(DATASTORE_PREFIX):
        return None
    return normalized[len(DATASTORE_PREFIX) :]


def _iter_write_values(base_path: str, body: dict) -> Iterable[tuple[str, object]]:
    base = normalize_path(base_path).rstrip("/")
    for key, value in body.items():
        if key == "value":
            yield base, value
            continue
        if key.startswith("/"):
            yield normalize_path(key), value
            continue
        if base == "/datastore":
            yield normalize_path(key), value
        else:
            yield normalize_path(f"{base}/{key}"), value


def _match_score(
    pattern: tuple[str, ...], requested: tuple[str, ...]
) -> tuple[int, int] | None:
    if len(pattern) != len(requested):
        return None
    exact = 0
    placeholders = 0
    for pattern_segment, requested_segment in zip(pattern, requested):
        if pattern_segment == requested_segment:
            exact += 1
            continue
        if _placeholder_matches(pattern_segment, requested_segment):
            placeholders += 1
            continue
        return None
    return exact, -placeholders


def _placeholder_matches(pattern_segment: str, requested_segment: str) -> bool:
    match = SEGMENT_PLACEHOLDER_RE.fullmatch(pattern_segment)
    if match is not None:
        return _placeholder_value_matches(match.group(1), requested_segment)
    match = SEGMENT_PLACEHOLDER_RE.search(pattern_segment)
    if match is None:
        return False
    prefix = pattern_segment[: match.start()]
    suffix = pattern_segment[match.end() :]
    if not requested_segment.startswith(prefix) or not requested_segment.endswith(
        suffix
    ):
        return False
    value_end = (
        len(requested_segment) - len(suffix) if suffix else len(requested_segment)
    )
    value = requested_segment[len(prefix) : value_end]
    return _placeholder_value_matches(match.group(1), value)


def _placeholder_value_matches(name: str, requested_segment: str) -> bool:
    if name == "index":
        return requested_segment.isdecimal()
    if name == "uid":
        return bool(UID_RE.fullmatch(requested_segment))
    allowed = PLACEHOLDER_VALUES.get(name)
    if allowed is not None:
        return requested_segment in allowed
    return bool(requested_segment)


def _validate_value(path: str, entry: PathSchema, value: object) -> None:
    base_type, modifiers = _split_type(entry.value_type)
    if "opt" in modifiers and value is None:
        return
    if "list" in modifiers:
        _validate_string_parts(path, entry.value_type, value, base_type, min_parts=0)
        return
    if "pair" in modifiers:
        _validate_string_parts(
            path, entry.value_type, value, base_type, min_parts=2, max_parts=2
        )
        return
    if "enum" in modifiers:
        _validate_scalar(path, value, base_type)
        if entry.enum_values is not None and value not in entry.enum_values:
            allowed = ", ".join(str(item) for item in entry.enum_values)
            raise DatastoreValidationError(f"{path} must be one of: {allowed}")
    else:
        _validate_scalar(path, value, base_type)
    if "bool" in modifiers and value != 0 and value != 1:
        raise DatastoreValidationError(f"{path} must be 0 or 1")
    if entry.minimum is not None or entry.maximum is not None:
        assert isinstance(value, Real)
        if entry.minimum is not None and value < entry.minimum:
            raise DatastoreValidationError(f"{path} must be >= {entry.minimum}")
        if entry.maximum is not None and value > entry.maximum:
            raise DatastoreValidationError(f"{path} must be <= {entry.maximum}")


def _split_type(value_type: str) -> tuple[str, set[str]]:
    parts = value_type.split("_")
    return parts[0], set(parts[1:])


def _validate_scalar(path: str, value: object, base_type: str) -> None:
    if base_type == "string":
        if not isinstance(value, str):
            raise DatastoreValidationError(f"{path} must be a string")
        return
    if base_type == "semver":
        if not isinstance(value, str) or SEMVER_RE.fullmatch(value) is None:
            raise DatastoreValidationError(f"{path} must be a semver string")
        return
    if base_type == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise DatastoreValidationError(f"{path} must be an integer")
        return
    if base_type == "real":
        if isinstance(value, bool) or not isinstance(value, Real):
            raise DatastoreValidationError(f"{path} must be a number")
        if not math.isfinite(value):
            raise DatastoreValidationError(f"{path} must be a finite number")
        return
    raise DatastoreValidationError(
        f"{path} uses unsupported datastore type {base_type!r}"
    )


def _validate_string_parts(
    path: str,
    value_type: str,
    value: object,
    base_type: str,
    min_parts: int,
    max_parts: int | None = None,
) -> None:
    if not isinstance(value, str):
        raise DatastoreValidationError(
            f"{path} must be a colon-separated {value_type} string"
        )
    parts = [] if value == "" else value.split(":")
    if len(parts) < min_parts or (max_parts is not None and len(parts) > max_parts):
        raise DatastoreValidationError(
            f"{path} must contain {min_parts} colon-separated values"
        )
    for part in parts:
        parsed: object = part
        if base_type == "int":
            try:
                parsed = int(part, 10)
            except ValueError as exc:
                raise DatastoreValidationError(
                    f"{path} contains a non-integer list value"
                ) from exc
        _validate_scalar(path, parsed, base_type)
