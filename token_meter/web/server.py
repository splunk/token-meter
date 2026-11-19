"""Local-only HTTP server lifecycle."""

import sys
import threading
import time

try:
    import resource
except ImportError:  # Windows has no POSIX resource limits.
    resource = None

# launchd starts agents with a soft limit of 256 descriptors; a threaded server with
# SQLite ledgers and runtime readers needs headroom above that.
DESCRIPTOR_TARGET = 4096
BACKGROUND_RETRY_S = 30.0
BACKGROUND_MAX_RETRY_S = 600.0


def raise_descriptor_limit():
    """Raise the soft open-file limit toward DESCRIPTOR_TARGET, never above the hard limit."""
    if resource is None:
        return
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        target = DESCRIPTOR_TARGET if hard == resource.RLIM_INFINITY else min(DESCRIPTOR_TARGET, hard)
        if soft != resource.RLIM_INFINITY and soft < target:
            resource.setrlimit(resource.RLIMIT_NOFILE, (target, hard))
    except (OSError, ValueError):
        pass


def _default_log(message):
    print(message, file=sys.stderr, flush=True)


def supervise(target, *, retry_s=BACKGROUND_RETRY_S, max_retry_s=BACKGROUND_MAX_RETRY_S,
              log=_default_log, sleep=time.sleep, clock=time.monotonic):
    """Run a background loop forever; an unexpected exception restarts it after a pause.

    Repeated failures back off exponentially up to ``max_retry_s``, and a run that
    stayed healthy that long starts the backoff over. Only a new exception class is
    logged, by target and class name alone: messages can carry local paths.
    """
    name = getattr(target, "__name__", "background")
    delay, last_kind = retry_s, None
    while True:
        started = clock()
        try:
            target()
            return
        except Exception as error:  # noqa: BLE001 - a watcher must not die silently
            if clock() - started >= max_retry_s:
                delay, last_kind = retry_s, None
            kind = type(error).__name__
            if kind != last_kind:
                log("{} stopped on {}; restarting in {:g}s".format(name, kind, delay))
                last_kind = kind
            sleep(delay)
            delay = min(delay * 2, max_retry_s)


def serve_local(*, handler_class, server_class, port, background=(), output=print):
    raise_descriptor_limit()
    for target in background:
        threading.Thread(
            target=supervise, args=(target,), name=getattr(target, "__name__", None), daemon=True,
        ).start()
    server = server_class(("127.0.0.1", int(port)), handler_class)
    output("Token Meter live -> http://localhost:{}".format(port))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        output("\nstopped.")
