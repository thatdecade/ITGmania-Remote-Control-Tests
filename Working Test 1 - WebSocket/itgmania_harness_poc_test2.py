# itgmania_harness_poc_test.py
import asyncio
import csv
import json
import os
import time
import warnings
from dataclasses import dataclass
from typing import Any, Optional, Tuple, List, Dict

import websockets

warnings.filterwarnings("ignore", category=DeprecationWarning)

CMD_HELLO = 0x01
CMD_GET_STATUS = 0x10
CMD_GET_GROUPS = 0x11
CMD_GET_SONGS = 0x12
CMD_START_SONG = 0x20
CMD_PAUSE = 0x21
CMD_STOP = 0x22

RSP_HELLO = 0x81
RSP_GET_STATUS = 0x90
RSP_GET_GROUPS = 0x91
RSP_GET_SONGS = 0x92
RSP_START_SONG = 0xA0
RSP_PAUSE = 0xA1
RSP_STOP = 0xA2
RSP_ERROR = 0xFF


@dataclass
class CaseResult:
    name: str
    passed: bool
    details: str


def ts() -> str:
    return time.strftime("%H:%M:%S")


def encode_uint16_be(value: int) -> bytes:
    return bytes([(value >> 8) & 0xFF, value & 0xFF])


def encode_nt_string(text: str) -> bytes:
    return text.encode("utf-8") + b"\x00"


def build_packet(command_id: int, payload: bytes = b"") -> bytes:
    size = 1 + len(payload)
    return encode_uint16_be(size) + bytes([command_id]) + payload


def parse_packets(buffer: bytearray) -> List[Tuple[int, bytes]]:
    packets: List[Tuple[int, bytes]] = []
    while True:
        if len(buffer) < 3:
            return packets
        size = (buffer[0] << 8) | buffer[1]
        total_size = 2 + size
        if len(buffer) < total_size:
            return packets
        command_id = buffer[2]
        payload = bytes(buffer[3:total_size]) if size > 1 else b""
        del buffer[:total_size]
        packets.append((command_id, payload))
    return packets


def payload_to_json(payload: bytes) -> Any:
    if payload.endswith(b"\x00"):
        payload = payload[:-1]
    text = payload.decode("utf-8", errors="replace")
    return json.loads(text)


def is_gameplay_screen(screen: str) -> bool:
    return screen.startswith("ScreenGameplay")


def judgments_sum(judgments: Any) -> int:
    if not isinstance(judgments, dict):
        return 0
    total = 0
    for _, value in judgments.items():
        if isinstance(value, (int, float)):
            total += int(value)
    return total


def extract_numeric(status: dict, key: str) -> float:
    value = status.get(key)
    if isinstance(value, (int, float)):
        return float(value)
    return 0.0


def ensure_csv_header(file_obj, fieldnames: List[str]) -> csv.DictWriter:
    needs_header = False
    try:
        if file_obj.tell() == 0:
            needs_header = True
    except Exception:
        needs_header = True

    writer = csv.DictWriter(file_obj, fieldnames=fieldnames)
    if needs_header:
        writer.writeheader()
        file_obj.flush()
    return writer


class ItgSession:
    def __init__(self) -> None:
        self.websocket: Optional[Any] = None
        self.rx_buffer = bytearray()

        self.connected_event = asyncio.Event()
        self.ready_event = asyncio.Event()

        self.response_queues: Dict[int, asyncio.Queue[bytes]] = {}
        self.error_queue: asyncio.Queue[bytes] = asyncio.Queue()

    def attach(self, websocket: Any) -> None:
        self.websocket = websocket
        self.rx_buffer = bytearray()
        self.connected_event.set()
        self.ready_event.clear()
        self.response_queues = {}
        self.error_queue = asyncio.Queue()

    def detach(self) -> None:
        self.websocket = None
        self.connected_event.clear()
        self.ready_event.clear()

    def _get_response_queue(self, response_id: int) -> asyncio.Queue[bytes]:
        queue = self.response_queues.get(response_id)
        if queue is None:
            queue = asyncio.Queue()
            self.response_queues[response_id] = queue
        return queue

    async def recv_loop(self) -> None:
        if self.websocket is None:
            return
        try:
            async for message in self.websocket:
                if isinstance(message, str):
                    if message.startswith("HEARTBEAT|") or message.startswith("SCREEN|"):
                        print(f"{ts()} INFO {message}")
                        if message.startswith("HEARTBEAT|"):
                            self.ready_event.set()
                    else:
                        print(f"{ts()} INFO TEXT|{message}")
                    continue

                self.rx_buffer.extend(message)
                for command_id, payload in parse_packets(self.rx_buffer):
                    if command_id == RSP_ERROR:
                        await self.error_queue.put(payload)
                    else:
                        await self._get_response_queue(command_id).put(payload)

        except websockets.exceptions.ConnectionClosed as closed_exc:
            print(f"{ts()} DISCONNECTED code={closed_exc.code} reason={closed_exc.reason}")
        finally:
            self.detach()

    async def request(
        self,
        command_id: int,
        expected_response_id: int,
        payload: bytes = b"",
        timeout_seconds: float = 10.0
    ) -> Any:
        if self.websocket is None:
            raise RuntimeError("Not connected")

        print(f"{ts()} SEND 0x{command_id:02X} expecting 0x{expected_response_id:02X}")
        await self.websocket.send(build_packet(command_id, payload))

        expected_queue = self._get_response_queue(expected_response_id)
        end_time = time.perf_counter() + timeout_seconds

        while time.perf_counter() < end_time:
            remaining = max(0.1, end_time - time.perf_counter())

            expected_task = asyncio.create_task(expected_queue.get())
            error_task = asyncio.create_task(self.error_queue.get())

            done, pending = await asyncio.wait(
                {expected_task, error_task},
                timeout=remaining,
                return_when=asyncio.FIRST_COMPLETED
            )

            for task in pending:
                task.cancel()

            if not done:
                continue

            finished_task = next(iter(done))
            payload_bytes = finished_task.result()

            if finished_task is error_task:
                error_obj = payload_to_json(payload_bytes)
                raise RuntimeError(f"ITG RSP_ERROR: {error_obj}")

            return payload_to_json(payload_bytes)

        raise TimeoutError(f"Timed out waiting for response 0x{expected_response_id:02X}")


async def hello_with_retry(session: ItgSession, max_attempts: int = 3) -> Any:
    last_exception: Optional[Exception] = None
    timeouts = [10.0, 20.0, 30.0]

    for attempt_index in range(max_attempts):
        timeout_seconds = timeouts[min(attempt_index, len(timeouts) - 1)]
        try:
            return await session.request(CMD_HELLO, RSP_HELLO, timeout_seconds=timeout_seconds)
        except Exception as exc:
            last_exception = exc
            print(f"{ts()} WARN HELLO attempt {attempt_index + 1}/{max_attempts} failed: {exc}")
            await asyncio.sleep(1.0)

    raise RuntimeError(f"HELLO failed after {max_attempts} attempts: {last_exception}")


async def wait_for_music_select(session: ItgSession, timeout_seconds: float = 240.0) -> str:
    desired = {"ScreenSelectMusic", "ScreenSelectMusicCasual"}
    end_time = time.perf_counter() + timeout_seconds
    while time.perf_counter() < end_time:
        response_object = await session.request(CMD_GET_STATUS, RSP_GET_STATUS, timeout_seconds=10.0)
        status_object = response_object.get("status", {})
        screen = str(status_object.get("screen", ""))
        print(f"{ts()} INFO current_screen={screen}")
        if screen in desired:
            return screen
        await asyncio.sleep(0.5)
    raise TimeoutError("Timed out waiting for music select screen")


async def wait_for_gameplay(session: ItgSession, desired: bool, timeout_seconds: float = 30.0) -> bool:
    end_time = time.perf_counter() + timeout_seconds
    while time.perf_counter() < end_time:
        response_object = await session.request(CMD_GET_STATUS, RSP_GET_STATUS, timeout_seconds=10.0)
        status_object = response_object.get("status", {})
        screen = str(status_object.get("screen", ""))
        flag = bool(status_object.get("is_playing", False))
        inferred = flag or is_gameplay_screen(screen)
        print(f"{ts()} INFO is_playing={flag} inferred_playing={inferred} screen={screen}")
        if inferred == desired:
            return True
        await asyncio.sleep(0.5)
    return False


async def fetch_songs(session: ItgSession, max_count: int = 200) -> List[dict]:
    payload = encode_uint16_be(max_count) + encode_nt_string("")
    response_object = await session.request(CMD_GET_SONGS, RSP_GET_SONGS, payload=payload, timeout_seconds=30.0)
    songs = response_object.get("songs", [])
    if not isinstance(songs, list):
        return []
    usable: List[dict] = []
    for song in songs:
        if isinstance(song, dict):
            usable.append(song)
    return usable


def pick_song(songs: List[dict]) -> Tuple[str, str, str]:
    if not songs:
        raise RuntimeError("No songs returned")
    song = songs[0]
    song_dir = str(song.get("song_dir", ""))
    title = str(song.get("title", ""))
    diffs = song.get("difficulties", [])
    if not isinstance(diffs, list):
        diffs = []
    desired = "Difficulty_Easy"
    if diffs:
        if desired in diffs:
            difficulty = desired
        else:
            difficulty = str(diffs[0])
    else:
        difficulty = desired
    return song_dir, difficulty, title


async def collect_timeseries(
    session: ItgSession,
    writer: csv.DictWriter,
    csv_file,
    run_id: str,
    cycle_index: int,
    duration_seconds: float,
    poll_interval_seconds: float,
    flush_each_sample: bool
) -> Tuple[bool, str, int, Dict[str, float], List[str]]:
    start_perf = time.perf_counter()

    baseline_set = False
    baseline_score = 0.0
    baseline_combo = 0.0
    baseline_percent = 0.0
    baseline_judg = 0

    max_score = 0.0
    max_combo = 0.0
    max_percent = 0.0
    max_judg = 0

    samples = 0

    while (time.perf_counter() - start_perf) < duration_seconds:
        response_object = await session.request(CMD_GET_STATUS, RSP_GET_STATUS, timeout_seconds=10.0)
        status = response_object.get("status", {})
        if not isinstance(status, dict):
            status = {}

        screen = str(status.get("screen", ""))
        is_playing_flag = bool(status.get("is_playing", False))
        inferred_playing = is_playing_flag or is_gameplay_screen(screen)

        score = extract_numeric(status, "score_p1")
        combo = extract_numeric(status, "current_combo_p1")
        percent = extract_numeric(status, "percent_dp_p1")
        judg = judgments_sum(status.get("judgments_p1"))

        if not baseline_set:
            baseline_set = True
            baseline_score = score
            baseline_combo = combo
            baseline_percent = percent
            baseline_judg = judg

        max_score = max(max_score, score)
        max_combo = max(max_combo, combo)
        max_percent = max(max_percent, percent)
        max_judg = max(max_judg, judg)

        now_wall_time = time.strftime("%Y-%m-%d %H:%M:%S")
        elapsed_seconds = time.perf_counter() - start_perf

        writer.writerow({
            "wall_time": now_wall_time,
            "run_id": run_id,
            "cycle": cycle_index,
            "elapsed_seconds": f"{elapsed_seconds:.3f}",
            "screen": screen,
            "inferred_playing": int(inferred_playing),
            "score_p1": int(score),
            "combo_p1": int(combo),
            "percent_dp_p1": percent,
            "judgment_sum_p1": judg,
            "song_title": str(status.get("current_title", "")),
            "song_dir": str(status.get("current_song_dir", "")),
            "difficulty_p1": str(status.get("current_difficulty_p1", "")),
            "paused_known": int(bool(status.get("paused_known", False))),
            "paused": int(bool(status.get("paused", False))),
        })
        samples += 1

        if flush_each_sample:
            csv_file.flush()

        await asyncio.sleep(poll_interval_seconds)

    deltas: Dict[str, float] = {
        "score": max_score - baseline_score,
        "combo": max_combo - baseline_combo,
        "percent": max_percent - baseline_percent,
        "judgments": float(max_judg - baseline_judg),
    }
    changed = [name for name, delta in deltas.items() if delta > 0]
    passed = len(changed) > 0
    detail = f"samples={samples} changed={changed} deltas={[(k, deltas[k]) for k in ['score','combo','percent','judgments']]}"
    return passed, detail, samples, deltas, changed


async def run_cycle(
    session: ItgSession,
    writer: csv.DictWriter,
    csv_file,
    run_id: str,
    cycle_index: int,
    stats_duration_seconds: float,
    poll_interval_seconds: float,
    do_pause_resume: bool,
    flush_each_sample: bool
) -> Tuple[List[CaseResult], Dict[str, Any]]:
    results: List[CaseResult] = []
    cycle_summary: Dict[str, Any] = {
        "run_id": run_id,
        "cycle": cycle_index,
        "song_title": "",
        "song_dir": "",
        "difficulty": "",
        "stats_samples": 0,
        "score_delta": 0,
        "combo_delta": 0,
        "percent_delta": 0.0,
        "judgment_delta": 0,
    }

    # HELLO
    try:
        rsp = await hello_with_retry(session, max_attempts=3)
        results.append(CaseResult("hello", bool(rsp.get("ok")), json.dumps(rsp)))
    except Exception as exc:
        results.append(CaseResult("hello", False, str(exc)))
        return results, cycle_summary

    # Wait for music select
    try:
        print(f"{ts()} INFO Navigate to music select and join P1.")
        screen = await wait_for_music_select(session, timeout_seconds=240.0)
        results.append(CaseResult("reach music select", True, f"screen={screen}"))
    except Exception as exc:
        results.append(CaseResult("reach music select", False, str(exc)))
        return results, cycle_summary

    # Fetch songs
    try:
        songs = await fetch_songs(session, max_count=200)
        ok = len(songs) > 0
        results.append(CaseResult("get songs", ok, f"song_count={len(songs)}"))
        if not ok:
            return results, cycle_summary
    except Exception as exc:
        results.append(CaseResult("get songs", False, str(exc)))
        return results, cycle_summary

    # Choose song
    try:
        song_dir, difficulty, title = pick_song(songs)
        cycle_summary["song_title"] = title
        cycle_summary["song_dir"] = song_dir
        cycle_summary["difficulty"] = difficulty
        results.append(CaseResult("choose song", True, f"title={title} song_dir={song_dir} difficulty={difficulty}"))
    except Exception as exc:
        results.append(CaseResult("choose song", False, str(exc)))
        return results, cycle_summary

    # Start song
    try:
        start_payload = encode_nt_string(song_dir) + encode_nt_string(difficulty)
        rsp = await session.request(CMD_START_SONG, RSP_START_SONG, payload=start_payload, timeout_seconds=15.0)
        results.append(CaseResult("start song", bool(rsp.get("ok")), json.dumps(rsp)))
        if not rsp.get("ok"):
            return results, cycle_summary
    except Exception as exc:
        results.append(CaseResult("start song", False, str(exc)))
        return results, cycle_summary

    # Verify gameplay started
    try:
        started = await wait_for_gameplay(session, desired=True, timeout_seconds=30.0)
        results.append(CaseResult("verify gameplay started", started, ""))
        if not started:
            return results, cycle_summary
    except Exception as exc:
        results.append(CaseResult("verify gameplay started", False, str(exc)))
        return results, cycle_summary

    # Stats collection
    print(f"{ts()} INFO Stats collection running for {stats_duration_seconds}s. Step on the pad during this window.")
    try:
        passed, detail, samples, deltas, _changed = await collect_timeseries(
            session=session,
            writer=writer,
            csv_file=csv_file,
            run_id=run_id,
            cycle_index=cycle_index,
            duration_seconds=stats_duration_seconds,
            poll_interval_seconds=poll_interval_seconds,
            flush_each_sample=flush_each_sample
        )
        cycle_summary["stats_samples"] = samples
        cycle_summary["score_delta"] = int(deltas["score"])
        cycle_summary["combo_delta"] = int(deltas["combo"])
        cycle_summary["percent_delta"] = float(deltas["percent"])
        cycle_summary["judgment_delta"] = int(deltas["judgments"])
        results.append(CaseResult("live stats change", passed, detail))
    except Exception as exc:
        results.append(CaseResult("live stats change", False, str(exc)))

    # Pause/resume
    if do_pause_resume:
        try:
            rsp = await session.request(CMD_PAUSE, RSP_PAUSE, payload=bytes([1]), timeout_seconds=10.0)
            results.append(CaseResult("pause", bool(rsp.get("ok")), json.dumps(rsp)))
        except Exception as exc:
            results.append(CaseResult("pause", False, str(exc)))

        try:
            rsp = await session.request(CMD_PAUSE, RSP_PAUSE, payload=bytes([0]), timeout_seconds=10.0)
            results.append(CaseResult("resume", bool(rsp.get("ok")), json.dumps(rsp)))
        except Exception as exc:
            results.append(CaseResult("resume", False, str(exc)))

    # Stop
    try:
        rsp = await session.request(CMD_STOP, RSP_STOP, timeout_seconds=15.0)
        results.append(CaseResult("stop", bool(rsp.get("ok")), json.dumps(rsp)))
    except Exception as exc:
        results.append(CaseResult("stop", False, str(exc)))
        return results, cycle_summary

    # Verify stopped
    try:
        stopped = await wait_for_gameplay(session, desired=False, timeout_seconds=45.0)
        results.append(CaseResult("verify stopped", stopped, ""))
    except Exception as exc:
        results.append(CaseResult("verify stopped", False, str(exc)))

    return results, cycle_summary


async def main_async(cycles: int, stats_seconds: float, poll_interval: float, do_pause_resume: bool) -> int:
    session = ItgSession()
    run_id = time.strftime("%Y%m%d_%H%M%S")

    timeseries_csv_path = "harness_poc_timeseries.csv"
    cycles_csv_path = "harness_poc_cycles.csv"

    timeseries_fieldnames = [
        "wall_time",
        "run_id",
        "cycle",
        "elapsed_seconds",
        "screen",
        "inferred_playing",
        "score_p1",
        "combo_p1",
        "percent_dp_p1",
        "judgment_sum_p1",
        "song_title",
        "song_dir",
        "difficulty_p1",
        "paused_known",
        "paused",
    ]

    cycles_fieldnames = [
        "wall_time",
        "run_id",
        "cycle",
        "passed_all",
        "song_title",
        "song_dir",
        "difficulty",
        "stats_samples",
        "score_delta",
        "combo_delta",
        "percent_delta",
        "judgment_delta",
        "case_results_json",
    ]

    timeseries_file = open(timeseries_csv_path, "a", newline="", encoding="utf-8")
    timeseries_writer = ensure_csv_header(timeseries_file, timeseries_fieldnames)

    cycles_file = open(cycles_csv_path, "a", newline="", encoding="utf-8")
    cycles_writer = ensure_csv_header(cycles_file, cycles_fieldnames)

    flush_each_sample = False  # Set True if you want each row flushed immediately.

    async def handler(websocket) -> None:
        print(f"{ts()} CONNECTED ITGmania client connected")
        session.attach(websocket)
        await session.recv_loop()

    async with websockets.serve(handler, host="127.0.0.1", port=8765, ping_interval=None):
        print(f"{ts()} LISTENING ws://127.0.0.1:8765")
        print(f"{ts()} INFO CSV logging to {timeseries_csv_path} (append) run_id={run_id}")
        print(f"{ts()} INFO Cycle summary logging to {cycles_csv_path} (append)")

        cycle_index = 1
        while True:
            print(f"{ts()} INFO Waiting for ITGmania to connect...")
            await session.connected_event.wait()

            try:
                await asyncio.wait_for(session.ready_event.wait(), timeout=15.0)
            except asyncio.TimeoutError:
                print(f"{ts()} WARN Connected but no heartbeat yet. Waiting for reconnect.")
                await asyncio.sleep(1.0)
                continue

            while session.connected_event.is_set():
                if cycle_index > cycles:
                    print(f"{ts()} INFO Completed requested cycles={cycles}.")
                    timeseries_file.flush()
                    cycles_file.flush()
                    timeseries_file.close()
                    cycles_file.close()
                    return 0

                print(f"{ts()} INFO Starting cycle {cycle_index}/{cycles}")

                try:
                    results, cycle_summary = await run_cycle(
                        session=session,
                        writer=timeseries_writer,
                        csv_file=timeseries_file,
                        run_id=run_id,
                        cycle_index=cycle_index,
                        stats_duration_seconds=stats_seconds,
                        poll_interval_seconds=poll_interval,
                        do_pause_resume=do_pause_resume,
                        flush_each_sample=flush_each_sample
                    )
                except Exception as exc:
                    print(f"{ts()} FAIL Cycle exception: {exc}")
                    break

                passed_all = True
                print(f"{ts()} INFO CYCLE RESULTS {cycle_index}")
                for case in results:
                    status = "PASS" if case.passed else "FAIL"
                    line = f"{status}: {case.name}"
                    if case.details:
                        line += f" | {case.details}"
                    print(line)
                    if not case.passed:
                        passed_all = False

                cycles_writer.writerow({
                    "wall_time": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "run_id": run_id,
                    "cycle": cycle_index,
                    "passed_all": int(passed_all),
                    "song_title": cycle_summary.get("song_title", ""),
                    "song_dir": cycle_summary.get("song_dir", ""),
                    "difficulty": cycle_summary.get("difficulty", ""),
                    "stats_samples": int(cycle_summary.get("stats_samples", 0)),
                    "score_delta": int(cycle_summary.get("score_delta", 0)),
                    "combo_delta": int(cycle_summary.get("combo_delta", 0)),
                    "percent_delta": float(cycle_summary.get("percent_delta", 0.0)),
                    "judgment_delta": int(cycle_summary.get("judgment_delta", 0)),
                    "case_results_json": json.dumps([case.__dict__ for case in results], ensure_ascii=True),
                })

                print(f"{ts()} INFO Cycle {cycle_index} complete passed_all={passed_all}")

                timeseries_file.flush()
                cycles_file.flush()

                cycle_index += 1
                await asyncio.sleep(1.0)

            print(f"{ts()} WARN Connection dropped. Waiting for reconnect.")

    return 0


def parse_args() -> Tuple[int, float, float, bool]:
    import sys

    cycles = 5
    stats_seconds = 12.0
    poll_interval = 0.25
    do_pause_resume = True

    # Usage:
    #   python itgmania_harness_poc_test.py [cycles] [stats_seconds] [poll_interval] [pause0or1]
    if len(sys.argv) >= 2:
        cycles = int(sys.argv[1])
    if len(sys.argv) >= 3:
        stats_seconds = float(sys.argv[2])
    if len(sys.argv) >= 4:
        poll_interval = float(sys.argv[3])
    if len(sys.argv) >= 5:
        do_pause_resume = bool(int(sys.argv[4]))

    return cycles, stats_seconds, poll_interval, do_pause_resume


def main() -> int:
    cycles, stats_seconds, poll_interval, do_pause_resume = parse_args()
    try:
        return asyncio.run(main_async(cycles, stats_seconds, poll_interval, do_pause_resume))
    except KeyboardInterrupt:
        print("Stopped.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
