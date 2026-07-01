#!/usr/bin/env python3
"""Validate foreground preemption while a MOTU datastore long-poll is held."""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from motu_proxy.datastore import (  # noqa: E402
    DEFAULT_FOREGROUND_PREEMPTION_BUDGET_MS,
    DatastoreConfig,
    DatastoreCoordinator,
    MotuUsbDatastore,
)
from motu_proxy.device import DEFAULT_DEVFS_ROOT, DEFAULT_SYSFS_ROOT, find_motu_device  # noqa: E402
from motu_proxy.parser import DatastorePayload  # noqa: E402
from motu_proxy.protocol import DEFAULT_MESSAGE_SEQ, DEFAULT_SEQ_START, MOTU_AVB_PID, MOTU_VID  # noqa: E402
from motu_proxy.transports.usbfs import UsbFsTransport  # noqa: E402


@dataclass(frozen=True)
class WriteEvent:
    timestamp: float
    data: bytes


class RecordingTransport:
    def __init__(self, wrapped: UsbFsTransport) -> None:
        self.wrapped = wrapped
        self._condition = threading.Condition()
        self._writes: list[WriteEvent] = []

    @property
    def max_packet_size(self) -> int:
        return self.wrapped.max_packet_size

    def bulk_write(self, data: bytes) -> int:
        written = self.wrapped.bulk_write(data)
        with self._condition:
            self._writes.append(WriteEvent(time.monotonic(), data))
            self._condition.notify_all()
        return written

    def bulk_read(self, size: int | None = None, timeout_ms: int | None = None) -> bytes:
        return self.wrapped.bulk_read(size=size, timeout_ms=timeout_ms)

    def begin_cancellable_bulk_read(self, size: int | None = None, timeout_ms: int | None = None):
        return self.wrapped.begin_cancellable_bulk_read(size=size, timeout_ms=timeout_ms)

    def wait_for_write(
        self,
        predicate: Callable[[WriteEvent], bool],
        timeout_s: float,
    ) -> WriteEvent | None:
        deadline = time.monotonic() + timeout_s
        with self._condition:
            while True:
                for event in self._writes:
                    if predicate(event):
                        return event
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._condition.wait(remaining)


def wait_for_active_poll(coordinator: DatastoreCoordinator, timeout_s: float) -> bool:
    deadline = time.monotonic() + timeout_s
    with coordinator._condition:
        while coordinator._active_poll_read is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            coordinator._condition.wait(remaining)
        return True


def payload_value(payload: DatastorePayload) -> object | None:
    body = payload.body.strip()
    if not body:
        return None
    decoded = json.loads(body.decode("utf-8"))
    if isinstance(decoded, dict) and "value" in decoded:
        return decoded["value"]
    return decoded


def validate_foreground_read(
    coordinator: DatastoreCoordinator,
    recorder: RecordingTransport,
    serial: str | None,
    budget_ms: int,
    cycle: int,
) -> float:
    if not wait_for_active_poll(coordinator, timeout_s=5):
        raise RuntimeError(f"cycle {cycle}: background poller did not enter an active native hold")
    results: list[DatastorePayload] = []
    errors: list[BaseException] = []
    started = time.monotonic()

    def read_uid() -> None:
        try:
            results.append(coordinator.read("/datastore/uid"))
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=read_uid, name=f"foreground-read-{cycle}")
    thread.start()
    event = recorder.wait_for_write(
        lambda item: item.timestamp >= started and b"/datastore/uid" in item.data,
        timeout_s=max(1.0, budget_ms / 1000),
    )
    if event is None:
        raise RuntimeError(f"cycle {cycle}: foreground read was not dispatched within {budget_ms} ms")
    dispatch_ms = (event.timestamp - started) * 1000
    thread.join(timeout=5)
    if thread.is_alive():
        raise RuntimeError(f"cycle {cycle}: foreground read did not complete")
    if errors:
        raise RuntimeError(f"cycle {cycle}: foreground read failed: {errors[0]}")
    value = payload_value(results[0])
    if serial is not None and value != serial:
        raise RuntimeError(f"cycle {cycle}: /datastore/uid returned {value!r}, expected {serial!r}")
    if not wait_for_active_poll(coordinator, timeout_s=5):
        raise RuntimeError(f"cycle {cycle}: background poller did not resume after foreground read")
    return dispatch_ms


def validate_idempotent_write(
    coordinator: DatastoreCoordinator,
    recorder: RecordingTransport,
    budget_ms: int,
) -> tuple[str, float]:
    current = coordinator.read("/datastore/host/os")
    body = json.dumps({"value": payload_value(current)}, separators=(",", ":"))
    if not wait_for_active_poll(coordinator, timeout_s=5):
        raise RuntimeError("write: background poller did not enter an active native hold")
    results: list[DatastorePayload] = []
    errors: list[BaseException] = []
    started = time.monotonic()

    def write_host_os() -> None:
        try:
            results.append(coordinator.post("/datastore/host/os", body))
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=write_host_os, name="foreground-write")
    thread.start()
    event = recorder.wait_for_write(
        lambda item: item.timestamp >= started and b"/datastore/host/os" in item.data,
        timeout_s=max(1.0, budget_ms / 1000),
    )
    if event is None:
        raise RuntimeError(f"write: foreground write was not dispatched within {budget_ms} ms")
    dispatch_ms = (event.timestamp - started) * 1000
    thread.join(timeout=10)
    if thread.is_alive():
        raise RuntimeError("write: foreground write did not complete")
    if errors:
        raise RuntimeError(f"write: foreground write failed: {errors[0]}")
    if not results:
        raise RuntimeError("write: no response captured")
    if not wait_for_active_poll(coordinator, timeout_s=5):
        raise RuntimeError("write: background poller did not resume after foreground write")
    return body, dispatch_ms


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vid", type=lambda value: int(value, 0), default=MOTU_VID)
    parser.add_argument("--pid", type=lambda value: int(value, 0), default=MOTU_AVB_PID)
    parser.add_argument("--serial")
    parser.add_argument("--interface", type=lambda value: int(value, 0))
    parser.add_argument("--ep-out", type=lambda value: int(value, 0))
    parser.add_argument("--ep-in", type=lambda value: int(value, 0))
    parser.add_argument("--timeout-ms", type=int, default=600)
    parser.add_argument("--seq-start", type=lambda value: int(value, 0), default=DEFAULT_SEQ_START)
    parser.add_argument("--message-seq", type=int, default=DEFAULT_MESSAGE_SEQ)
    parser.add_argument("--no-init", action="store_true")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--sysfs-root", default=str(DEFAULT_SYSFS_ROOT))
    parser.add_argument("--devfs-root", default=str(DEFAULT_DEVFS_ROOT))
    parser.add_argument("--cycles", type=int, default=3)
    parser.add_argument(
        "--preemption-budget-ms",
        type=int,
        default=DEFAULT_FOREGROUND_PREEMPTION_BUDGET_MS,
    )
    parser.add_argument(
        "--include-idempotent-write",
        action="store_true",
        help="also write the current /datastore/host/os value back to the device",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = DatastoreConfig(
        vid=args.vid,
        pid=args.pid,
        serial=args.serial,
        interface=args.interface,
        ep_out=args.ep_out,
        ep_in=args.ep_in,
        timeout_ms=args.timeout_ms,
        seq_start=args.seq_start,
        message_seq=args.message_seq,
        no_init=args.no_init,
        debug=args.debug,
        sysfs_root=Path(args.sysfs_root),
        devfs_root=Path(args.devfs_root),
    )
    device = find_motu_device(
        config.vid,
        config.pid,
        serial=config.serial,
        sysfs_root=config.sysfs_root,
        devfs_root=config.devfs_root,
        interface=config.interface,
        ep_out=config.ep_out,
        ep_in=config.ep_in,
    )
    with UsbFsTransport(device, timeout_ms=config.timeout_ms, debug=config.debug) as usb_transport:
        recorder = RecordingTransport(usb_transport)
        datastore = MotuUsbDatastore(
            recorder,
            seq_start=config.seq_start,
            message_seq=config.message_seq,
        )
        if not config.no_init:
            datastore.init()
        coordinator = DatastoreCoordinator(
            datastore,
            foreground_preemption_budget_ms=args.preemption_budget_ms,
            poll_interval_s=0,
        )
        if not coordinator.foreground_preemptive_native_long_poll_available:
            raise RuntimeError("transport reports foreground-preemptive native long-poll as unavailable")
        initial = coordinator.read("/datastore")
        print(f"initial_etag={initial.etag}")
        coordinator.start()
        try:
            read_dispatch_ms = [
                validate_foreground_read(
                    coordinator,
                    recorder,
                    config.serial,
                    args.preemption_budget_ms,
                    cycle,
                )
                for cycle in range(1, args.cycles + 1)
            ]
            write_dispatch_ms: float | None = None
            write_body: str | None = None
            if args.include_idempotent_write:
                write_body, write_dispatch_ms = validate_idempotent_write(
                    coordinator,
                    recorder,
                    args.preemption_budget_ms,
                )
            print(
                "PASS: "
                f"read_cycles={args.cycles} "
                f"max_read_dispatch_ms={max(read_dispatch_ms):.1f} "
                f"budget_ms={args.preemption_budget_ms}"
            )
            if write_dispatch_ms is not None:
                print(
                    "PASS: "
                    f"idempotent_write_path=/datastore/host/os "
                    f"body={write_body} "
                    f"write_dispatch_ms={write_dispatch_ms:.1f} "
                    f"budget_ms={args.preemption_budget_ms}"
                )
        finally:
            coordinator.close(timeout=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
