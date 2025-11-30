import argparse
import logging
import time
import concurrent.futures
from pathlib import Path
from typing import Iterable
import requests
import config

from .utils import ResponseSink, setup_logging
from .models import parse_raw_request
from .placeholders import PlaceholderResolver
from .proxies import load_proxies, check_proxies, ProxyPool, ProxyExhausted
from .network import send_with_proxy_failover

def iter_request_files() -> Iterable[Path]:
    folder = Path(config.REQUESTS_DIR)
    folder.mkdir(parents=True, exist_ok=True)
    for path in sorted(folder.glob("*.txt")):
        # Ignore example files unless the user renames them.
        if path.name.lower().startswith("example"):
            continue
        yield path

def warn_no_proxies(delay: bool, source: Path, direct_flag: bool) -> None:
    banner = "\n".join(
        [
            "=" * 70,
            "  NO PROXIES FOUND — RUNNING DIRECT",
            f"  File: {source}",
            "  Add proxies or use --direct to skip the startup delay.",
            "=" * 70,
        ]
    )
    logging.warning(banner)
    if delay:
        logging.warning("Starting in 10 seconds because proxies are missing...")
        time.sleep(10)
    elif direct_flag:
        logging.warning("--direct flag: running direct with no delay.")

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--direct",
        action="store_true",
        help="Ignore proxies.txt and send directly.",
    )
    parser.add_argument(
        "--proxy-file",
        default=config.PROXIES_FILE,
        type=Path,
        help="Path to proxy list file (default: config.PROXIES_FILE).",
    )
    parser.add_argument(
        "--response",
        nargs="?",
        const=True,
        metavar="FILE",
        help="Dump responses. Without FILE -> print to console. With FILE -> append to responses/FILE (or abs path).",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check all proxies in parallel and exit.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=10,
        help="Number of parallel workers for sending requests (default: 10).",
    )
    return parser.parse_args()

def process_single_request(
    path: Path,
    resolver: PlaceholderResolver,
    session: requests.Session,
    pool: ProxyPool,
    response_sink: ResponseSink
) -> None:
    try:
        raw_text = path.read_text(encoding="utf-8")
        raw_text = resolver.replace(raw_text)
        parsed = parse_raw_request(raw_text)
        # Note: Session is not thread-safe if we modify it, but requests.Session 
        # is generally thread-safe for making requests.
        # However, to be purely safe with cookies/adapters, one might want distinct sessions 
        # or a thread-local session. For raw replaying, sharing is usually fine or desired (cookies).
        response = send_with_proxy_failover(parsed, session, pool)
        if response_sink.enabled():
            response_sink.write(response)
    except Exception as exc:
        logging.error("Failed to send %s: %s", path.name, exc)

def run_loop(args: argparse.Namespace) -> None:
    session = requests.Session()

    proxies = [] if args.direct else load_proxies(Path(args.proxy_file))
    if args.direct and proxies:
        logging.info(
            "--direct enabled: ignoring %s proxies from %s",
            len(proxies),
            args.proxy_file,
        )
        proxies = []

    pool = ProxyPool(
        proxies,
        ignore_proxies=args.direct,
        file_path=None if args.direct else Path(args.proxy_file),
    )
    if pool.has_proxies():
        logging.info("Loaded proxies: %s (from %s)", len(proxies), args.proxy_file)
    else:
        warn_no_proxies(delay=not args.direct, source=Path(args.proxy_file), direct_flag=args.direct)

    logging.info("Starting sender. Reading from %s", config.REQUESTS_DIR)
    logging.info("Parallel workers: %s", args.workers)

    resolver = PlaceholderResolver(
        folder=Path(config.PLACEHOLDERS_DIR),
        rotation=config.PLACEHOLDER_ROTATION,
    )
    response_sink = ResponseSink(args.response)
    if response_sink.enabled():
        mode = "console" if response_sink.mode == "console" else f"file={response_sink.path}"
        logging.info("Response dump enabled (%s)", mode)

    try:
        while True:
            files = list(iter_request_files())
            if not files:
                logging.warning("No *.txt request files found in %s, stopping.", config.REQUESTS_DIR)
                break

            with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
                futures = [
                    executor.submit(process_single_request, path, resolver, session, pool, response_sink)
                    for path in files
                ]
                # Wait for all to complete
                concurrent.futures.wait(futures)

            time.sleep(config.INTERVAL_SECONDS)
    except KeyboardInterrupt:
        logging.info("Interrupted with Ctrl+C, exiting cleanly.")
    except ProxyExhausted as exc:
        logging.error("%s. Terminating.", exc)
    finally:
        session.close()
def main() -> None:
    setup_logging()
    args = parse_args()
    if args.check:
        if args.direct:
            logging.warning("--check with --direct: nothing to test (no proxies).")
            return
        proxies = load_proxies(Path(args.proxy_file))
        check_proxies(proxies, dest_file=Path(args.proxy_file))
        return
    run_loop(args)
