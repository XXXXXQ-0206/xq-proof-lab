from __future__ import annotations

import subprocess
import time
import shlex
import sys
from collections import deque
from collections.abc import Callable
from collections.abc import Sequence
from dataclasses import dataclass
from queue import Empty
from threading import Event
from queue import Queue
from threading import Thread


class UciEngineError(RuntimeError):
    pass


class UciEngineTimeout(UciEngineError):
    def __init__(
        self,
        message: str,
        *,
        lines: Sequence[str] = (),
        stderr_tail: Sequence[str] = (),
    ) -> None:
        super().__init__(message)
        self.lines = tuple(lines)
        self.stderr_tail = tuple(stderr_tail)


_EOF = object()


def starts_with_uci_token(command: str, token: str) -> bool:
    return command == token or command.startswith(token + " ")


def extract_go_searchmoves(command: str) -> tuple[str, ...]:
    tokens = command.split()
    if not tokens or tokens[0] != "go":
        return ()
    searchmoves: list[str] = []
    index = 1
    while index < len(tokens):
        token = tokens[index].lower()
        if token == "searchmoves":
            index += 1
            while index < len(tokens) and tokens[index].lower() not in _GO_KEYWORDS_AFTER_SEARCHMOVES:
                searchmoves.append(tokens[index])
                index += 1
        else:
            index += 1
    return tuple(searchmoves)


def split_engine_command(command: str) -> list[str]:
    command = command.strip()
    if not command:
        raise ValueError("engine command must not be empty")
    if sys.platform == "win32":
        return _split_windows_command(command)
    return shlex.split(command)


def _split_windows_command(command: str) -> list[str]:
    import ctypes

    argc = ctypes.c_int()
    command_line_to_argv = ctypes.windll.shell32.CommandLineToArgvW
    command_line_to_argv.argtypes = (ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int))
    command_line_to_argv.restype = ctypes.POINTER(ctypes.c_wchar_p)
    argv = command_line_to_argv(command, ctypes.byref(argc))
    if not argv:
        raise ValueError("failed to parse engine command")
    try:
        return [argv[index] for index in range(argc.value)]
    finally:
        local_free = ctypes.windll.kernel32.LocalFree
        local_free.argtypes = (ctypes.c_void_p,)
        local_free.restype = ctypes.c_void_p
        local_free(argv)


@dataclass(frozen=True, slots=True)
class PerftResult:
    nodes: int
    raw_lines: tuple[str, ...]
    divide: dict[str, int]


def extract_perft_divide(lines: Sequence[str]) -> dict[str, int]:
    divide: dict[str, int] = {}
    for line in lines:
        if line.startswith("Nodes searched:"):
            break
        parsed = _parse_perft_divide_line(line)
        if parsed is None:
            continue
        move, nodes = parsed
        divide[move] = nodes
    return divide


def _parse_perft_divide_line(line: str) -> tuple[str, int] | None:
    text = line.strip()
    if not text:
        return None
    if ":" in text:
        move, value = text.split(":", 1)
    else:
        parts = text.split()
        if len(parts) != 2:
            return None
        move, value = parts
    move = move.strip()
    value = value.strip()
    if len(move) != 4:
        return None
    try:
        return move, int(value)
    except ValueError:
        return None


def extract_pv_moves(lines: Sequence[str]) -> tuple[str, ...]:
    latest_by_multipv: dict[int, str] = {}
    for line in lines:
        tokens = line.split()
        if not _is_search_info_pv_tokens(tokens):
            continue
        pv_index = tokens.index("pv")
        if pv_index + 1 >= len(tokens):
            continue
        root_move = tokens[pv_index + 1]

        multipv = 1
        if "multipv" in tokens:
            multipv_index = tokens.index("multipv")
            if multipv_index + 1 < len(tokens):
                try:
                    multipv = int(tokens[multipv_index + 1])
                except ValueError:
                    multipv = 1
        latest_by_multipv[multipv] = root_move

    moves: list[str] = []
    seen: set[str] = set()
    for multipv in sorted(latest_by_multipv):
        move = latest_by_multipv[multipv]
        if move not in seen:
            moves.append(move)
            seen.add(move)
    return tuple(moves)


def _is_search_info_pv_tokens(tokens: Sequence[str]) -> bool:
    return bool(tokens) and tokens[0] == "info" and len(tokens) > 1 and tokens[1] != "string" and "pv" in tokens


_GO_KEYWORDS_AFTER_SEARCHMOVES = {
    "depth",
    "nodes",
    "movetime",
    "wtime",
    "btime",
    "winc",
    "binc",
    "movestogo",
    "mate",
    "infinite",
    "ponder",
}


class UciEngine:
    def __init__(self, command: Sequence[str], timeout: float = 5.0) -> None:
        if not command:
            raise ValueError("command must not be empty")
        self.command = list(command)
        self.timeout = timeout
        self.process: subprocess.Popen[str] | None = None
        self._stdout_thread: Thread | None = None
        self._stdout_stop = Event()
        self._stdout_queue: Queue[str | BaseException | object] = Queue()
        self._stderr_thread: Thread | None = None
        self._stderr_stop = Event()
        self._stderr_tail: deque[str] = deque(maxlen=32)

    def __enter__(self) -> "UciEngine":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def start(self) -> None:
        if self.process is not None:
            return
        self.process = subprocess.Popen(
            self.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self._stdout_stop.clear()
        self._stdout_queue = Queue()
        if self.process.stdout is not None:
            self._stdout_thread = Thread(target=self._drain_stdout, daemon=True)
            self._stdout_thread.start()
        self._stderr_stop.clear()
        if self.process.stderr is not None:
            self._stderr_thread = Thread(target=self._drain_stderr, daemon=True)
            self._stderr_thread.start()

    def close(self) -> None:
        if self.process is None:
            return
        process = self.process
        try:
            if process.stdin:
                self._send("quit")
            process.wait(timeout=1.0)
        except Exception:
            process.kill()
            process.wait(timeout=1.0)
        finally:
            self._stdout_stop.set()
            if self._stdout_thread is not None:
                self._stdout_thread.join(timeout=0.2)
                self._stdout_thread = None
            self._stderr_stop.set()
            if self._stderr_thread is not None:
                self._stderr_thread.join(timeout=0.2)
                self._stderr_thread = None
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None and not stream.closed:
                    try:
                        stream.close()
                    except OSError:
                        pass
            self.process = None

    def initialize(self) -> None:
        self.start()
        self._send("uci")
        self._read_until(lambda line: line == "uciok")
        self.wait_ready()

    def wait_ready(self) -> None:
        self._send("isready")
        self._read_until(lambda line: line == "readyok")

    def new_game(self) -> None:
        self._send("ucinewgame")
        self.wait_ready()

    def set_option(self, name: str, value: str | int | bool | None = None) -> None:
        if value is None:
            self._send(f"setoption name {name}")
            return
        normalized = str(value).lower() if isinstance(value, bool) else str(value)
        self._send(f"setoption name {name} value {normalized}")

    def set_position(self, fen: str, moves: Sequence[str] = ()) -> None:
        command = f"position fen {fen}"
        if moves:
            command += " moves " + " ".join(moves)
        self._send(command)

    def set_position_command(self, command: str) -> None:
        if not command.startswith("position "):
            raise ValueError("position command must start with 'position '")
        self._send(command)

    def go_perft(self, depth: int) -> PerftResult:
        if depth < 0:
            raise ValueError("depth must be non-negative")
        self._send(f"go perft {depth}")
        lines = self._read_until(lambda line: line.startswith("Nodes searched:"))
        for line in reversed(lines):
            if line.startswith("Nodes searched:"):
                return PerftResult(
                    int(line.split(":", 1)[1].strip()),
                    tuple(lines),
                    extract_perft_divide(lines),
                )
        raise UciEngineError("engine did not return a Nodes searched line")

    def go_depth(self, depth: int) -> tuple[str, tuple[str, ...]]:
        if depth < 0:
            raise ValueError("depth must be non-negative")
        return self.go(f"go depth {depth}")

    def go(self, command: str) -> tuple[str, tuple[str, ...]]:
        if not starts_with_uci_token(command, "go"):
            raise ValueError("go command must start with 'go'")
        self._send(command)
        try:
            lines = self._read_until(lambda line: line.startswith("bestmove "))
        except UciEngineTimeout as exc:
            initial_lines = list(exc.lines)
            if not _is_timeout_error(exc):
                raise
            self.stop()
            try:
                late_lines = self._read_until(
                    lambda line: line.startswith("bestmove "),
                    timeout_seconds=_stop_grace_seconds(self.timeout),
                )
            except UciEngineTimeout as late_exc:
                if self.process is not None and self.process.poll() is not None:
                    raise UciEngineError(
                        _engine_exit_message(
                            self.process.returncode,
                            tuple(self._stderr_tail),
                        )
                    ) from late_exc
                raise
            lines = initial_lines + late_lines
        bestmove = _parse_bestmove(lines[-1])
        return bestmove, tuple(lines)

    def stop(self) -> None:
        self._send("stop")

    def _send(self, command: str) -> None:
        if self.process is None:
            raise UciEngineError("engine is not started")
        if self.process.stdin is None:
            raise UciEngineError("engine stdin is closed")
        try:
            self.process.stdin.write(command + "\n")
            self.process.stdin.flush()
        except OSError as exc:
            returncode = self.process.poll()
            if returncode is not None:
                raise UciEngineError(
                    _engine_exit_message(returncode, tuple(self._stderr_tail))
                ) from exc
            raise UciEngineError(f"failed sending command to engine: {command!r}") from exc

    def _read_until(
        self,
        predicate: Callable[[str], bool],
        *,
        timeout_seconds: float | None = None,
    ) -> list[str]:
        if self.process is None:
            raise UciEngineError("engine stdout is closed")
        deadline = time.monotonic() + (self.timeout if timeout_seconds is None else timeout_seconds)
        lines: list[str] = []
        while time.monotonic() < deadline:
            timeout_left = max(0.0, deadline - time.monotonic())
            try:
                item = self._stdout_queue.get(timeout=timeout_left)
            except Empty:
                break
            if item is _EOF:
                if self.process.poll() is not None:
                    raise UciEngineError(
                        _engine_exit_message(self.process.returncode, tuple(self._stderr_tail))
                    )
                continue
            if isinstance(item, BaseException):
                raise UciEngineError(f"failed reading engine output: {item}") from item
            stripped = item.strip()
            if not stripped:
                continue
            lines.append(stripped)
            if predicate(stripped):
                return lines
        raise UciEngineTimeout(
            _timeout_message(lines, tuple(self._stderr_tail)),
            lines=tuple(lines),
            stderr_tail=tuple(self._stderr_tail),
        )

    def _drain_stdout(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        while not self._stdout_stop.is_set():
            try:
                line = self.process.stdout.readline()
            except BaseException as exc:  # pragma: no cover - defensive transport guard
                self._stdout_queue.put(exc)
                return
            if line == "":
                self._stdout_queue.put(_EOF)
                return
            self._stdout_queue.put(line)

    def _drain_stderr(self) -> None:
        assert self.process is not None and self.process.stderr is not None
        while not self._stderr_stop.is_set():
            line = self.process.stderr.readline()
            if line == "":
                return
            stripped = line.strip()
            if stripped:
                self._stderr_tail.append(stripped)


def _parse_bestmove(line: str) -> str:
    parts = line.split()
    if len(parts) < 2:
        raise UciEngineError(f"malformed bestmove line: {line!r}")
    return parts[1]


def _engine_exit_message(returncode: int | None, stderr_tail: tuple[str, ...]) -> str:
    message = f"engine exited with code {returncode}"
    if stderr_tail:
        message += f"; stderr tail: {list(stderr_tail)!r}"
    return message


def _timeout_message(lines: Sequence[str], stderr_tail: tuple[str, ...]) -> str:
    message = f"timed out waiting for engine output; got {lines!r}"
    if stderr_tail:
        message += f"; stderr tail: {list(stderr_tail)!r}"
    return message


def _is_timeout_error(exc: UciEngineError) -> bool:
    return str(exc).startswith("timed out waiting for engine output")


def _stop_grace_seconds(timeout: float) -> float:
    return 0.1
