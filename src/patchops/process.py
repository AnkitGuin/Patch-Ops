import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from threading import Thread
from time import monotonic, sleep
from typing import IO

from .security import isolated_env


@dataclass
class Result:
    code: int
    stdout: bytes
    stderr: bytes
    timed_out: bool
    truncated: bool


def run(
    argv: list[str],
    *,
    seconds: float,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    stdin: IO[bytes] | None = None,
    output: IO[bytes] | None = None,
    limit: int = 96_000,
    checkpoint: Callable[[], None] = lambda: None,
) -> Result:
    """Drain both pipes, cap retained output, and kill/reap on timeout or cancellation."""
    captured: list[bytearray] = [bytearray(), bytearray()]
    overflow = [False, False]

    def drain(pipe: IO[bytes], index: int) -> None:
        count = 0
        while chunk := pipe.read1(65536):  # type: ignore[attr-defined]
            if count + len(chunk) > limit:
                overflow[index] = True
            piece = chunk[: max(0, limit - count)]
            if output is not None and index == 0:
                output.write(piece)
            else:
                captured[index].extend(piece)
            count += len(chunk)

    with subprocess.Popen(
        argv,
        cwd=cwd,
        env=isolated_env() if env is None else env,
        stdin=stdin if stdin is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=os.name == "posix",
    ) as proc:
        assert proc.stdout is not None and proc.stderr is not None
        threads = [
            Thread(target=drain, args=(proc.stdout, 0), daemon=True),
            Thread(target=drain, args=(proc.stderr, 1), daemon=True),
        ]
        for thread in threads:
            thread.start()
        end = monotonic() + seconds
        timed_out = False
        try:
            while proc.poll() is None or any(thread.is_alive() for thread in threads):
                checkpoint()
                timed_out = monotonic() >= end
                if timed_out or (output is not None and overflow[0]):
                    break
                sleep(0.05)
        finally:
            if proc.poll() is None or any(thread.is_alive() for thread in threads):
                if sys.platform != "win32":
                    import signal

                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        proc.kill()
                else:
                    proc.kill()
            proc.wait()
            for thread in threads:
                thread.join(timeout=2)
    return Result(
        proc.returncode,
        bytes(captured[0]),
        bytes(captured[1]),
        timed_out,
        any(overflow),
    )
