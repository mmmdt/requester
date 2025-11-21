import argparse
import concurrent.futures
import logging
import random
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import requests
from colorama import Fore, Style, init as colorama_init

import config


colorama_init(autoreset=True)


class ColorFormatter(logging.Formatter):
    COLORS = {
        logging.DEBUG: Fore.CYAN,
        logging.INFO: Fore.GREEN,
        logging.WARNING: Fore.YELLOW,
        logging.ERROR: Fore.RED,
        logging.CRITICAL: Fore.RED + Style.BRIGHT,
    }

    def format(self, record: logging.LogRecord) -> str:  # type: ignore[override]
        color = self.COLORS.get(record.levelno, "")
        reset = Style.RESET_ALL
        time_str = self.formatTime(record, "%H:%M:%S")
        level = f"{record.levelname:<8}"
        msg = record.getMessage()
        return f"{Fore.WHITE}{time_str}{reset} {color}{level}{reset} {msg}"


handler = logging.StreamHandler()
handler.setFormatter(ColorFormatter())
logging.basicConfig(level=logging.INFO, handlers=[handler])


@dataclass
class ParsedRequest:
    method: str
    path: str
    headers: Dict[str, str]
    body: str


class ProxyPool:
    def __init__(
        self,
        proxies: List[str],
        ignore_proxies: bool = False,
        file_path: Optional[Path] = None,
    ) -> None:
        self._proxies = proxies
        self._index = 0
        self.ignore_proxies = ignore_proxies
        self._warned_empty = not proxies
        self._initial_count = len(proxies)
        self._exhausted = False
        self._current: Optional[str] = None
        self._file_path = file_path

    def has_proxies(self) -> bool:
        return (not self.ignore_proxies) and bool(self._proxies)

    def allow_direct_fallback(self) -> bool:
        return self.ignore_proxies or self._initial_count == 0

    def exhausted(self) -> bool:
        return self._exhausted

    def next_proxy(self) -> Optional[str]:
        if self._current and self._current in self._proxies:
            return self._current
        if not self.has_proxies():
            return None
        proxy = self._proxies[self._index % len(self._proxies)]
        self._index = (self._index + 1) % len(self._proxies)
        self._current = proxy
        return proxy

    def _persist(self) -> None:
        if not self._file_path:
            return
        try:
            self._file_path.parent.mkdir(parents=True, exist_ok=True)
            data = "\n".join(self._proxies)
            if self._proxies:
                data += "\n"
            self._file_path.write_text(data, encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            logging.error("Failed to update proxy file %s: %s", self._file_path, exc)

    def mark_bad(self, proxy: Optional[str]) -> None:
        if proxy is None:
            return
        try:
            idx = self._proxies.index(proxy)
        except ValueError:
            return
        self._proxies.pop(idx)
        if self._index > idx:
            self._index -= 1
        if proxy == self._current:
            self._current = None
        if not self._proxies:
            if not self.allow_direct_fallback():
                self._exhausted = True
                logging.error("Proxy list exhausted; stopping (no direct fallback).")
            elif not self._warned_empty:
                logging.warning("Proxy list is empty, running direct.")
                self._warned_empty = True
        self._persist()


class ProxyExhausted(Exception):
    """Raised when all proxies are dead and direct fallback is not allowed."""


def test_proxy(proxy_url: str) -> tuple[bool, str]:
    proxies = {"http": proxy_url, "https": proxy_url}
    attempts = [config.VERIFY_TLS]
    if config.VERIFY_TLS:
        attempts.append(False)

    last_error = "unknown error"
    for verify_flag in attempts:
        try:
            resp = requests.get(
                config.PROXY_CHECK_URL,
                proxies=proxies,
                timeout=config.TIMEOUT_SECONDS,
                verify=verify_flag,
            )
            if resp.ok:
                mode = "verify" if verify_flag else "no-verify"
                return True, f"HTTP {resp.status_code} ({mode})"
            return False, f"HTTP {resp.status_code}"
        except requests.exceptions.SSLError as exc:
            last_error = f"SSL error: {exc}"
            if verify_flag and config.VERIFY_TLS:
                continue  # retry without verification
        except requests.RequestException as exc:
            last_error = str(exc)
        break
    return False, last_error


def check_proxies(proxies: List[str], dest_file: Optional[Path] = None) -> None:
    if not proxies:
        logging.warning("No proxies to check.")
        return
    logging.info(
        "Checking %s proxies against %s (timeout=%ss)...",
        len(proxies),
        config.PROXY_CHECK_URL,
        config.TIMEOUT_SECONDS,
    )
    good: List[str] = []
    max_workers = min(len(proxies), config.PROXY_CHECK_WORKERS)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {executor.submit(test_proxy, proxy): proxy for proxy in proxies}
        for future in concurrent.futures.as_completed(future_map):
            proxy = future_map[future]
            try:
                ok, detail = future.result()
            except Exception as exc:  # noqa: BLE001
                logging.error("Proxy %s check raised: %s", proxy, exc)
                continue
            if ok:
                good.append(proxy)
                logging.info("OK   %s (%s)", proxy, detail)
            else:
                logging.error("BAD  %s (%s)", proxy, detail)
    logging.info("Proxy check finished: %s good / %s total.", len(good), len(proxies))
    if dest_file is not None:
        try:
            dest_file.parent.mkdir(parents=True, exist_ok=True)
            data = "\n".join(good)
            if good:
                data += "\n"
            dest_file.write_text(data, encoding="utf-8")
            logging.info(
                "Updated proxy file %s with %s working proxies (removed %s).",
                dest_file,
                len(good),
                len(proxies) - len(good),
            )
        except Exception as exc:  # noqa: BLE001
            logging.error("Failed to write proxy check results to %s: %s", dest_file, exc)
    if good:
        logging.info("Working proxies:\n%s", "\n".join(good))


class PlaceholderResolver:
    pattern = re.compile(r"\{([A-Za-z0-9_\-]+)\}")

    def __init__(self, folder: Path, rotation: str = "sequential") -> None:
        self.folder = folder
        self.rotation = rotation.lower()
        self.values: Dict[str, List[str]] = {}
        self.indexes: Dict[str, int] = {}
        folder.mkdir(parents=True, exist_ok=True)
        if self.rotation not in {"sequential", "random"}:
            logging.warning(
                "Unknown placeholder rotation '%s', falling back to 'sequential'",
                rotation,
            )
            self.rotation = "sequential"

    def _path_for(self, name: str) -> Path:
        direct = self.folder / name
        with_txt = self.folder / f"{name}.txt"
        if direct.exists():
            return direct
        if with_txt.exists():
            return with_txt
        return direct

    def _ensure_loaded(self, name: str) -> None:
        if name in self.values:
            return
        path = self._path_for(name)
        if not path.exists():
            raise ValueError(f"Placeholder '{name}' not found (expected {path} or {path}.txt)")
        lines = [
            line.strip()
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        if not lines:
            raise ValueError(f"Placeholder '{name}' has no values in {path}")
        self.values[name] = lines
        self.indexes.setdefault(name, 0)

    def _next_value(self, name: str) -> str:
        self._ensure_loaded(name)
        vals = self.values[name]
        if self.rotation == "random":
            return random.choice(vals)
        idx = self.indexes.get(name, 0) % len(vals)
        self.indexes[name] = (idx + 1) % len(vals)
        return vals[idx]

    def replace(self, text: str) -> str:
        names = set(self.pattern.findall(text))
        if not names:
            return text
        replacements = {name: self._next_value(name) for name in names}
        return self.pattern.sub(lambda m: replacements[m.group(1)], text)


def parse_raw_request(raw_text: str) -> ParsedRequest:
    """Convert raw HTTP text into a ParsedRequest object."""
    if not raw_text.strip():
        raise ValueError("request text is empty")

    head, body = _split_head_and_body(raw_text)
    head_lines = head.splitlines()
    if not head_lines:
        raise ValueError("missing request line")

    try:
        method, path, _ = head_lines[0].strip().split()
    except ValueError as exc:  # not enough values to unpack
        raise ValueError(f"cannot parse request line: {head_lines[0]}") from exc

    headers: Dict[str, str] = {}
    for line in head_lines[1:]:
        if not line.strip():
            continue
        if ":" not in line:
            raise ValueError(f"invalid header format: {line}")
        name, value = line.split(":", 1)
        headers[name.strip()] = value.strip()

    return ParsedRequest(method=method, path=path, headers=headers, body=body)


def _split_head_and_body(raw_text: str) -> tuple[str, str]:
    parts = re.split(r"\r?\n\r?\n", raw_text, maxsplit=1)
    head = parts[0].replace("\r", "")
    body = parts[1] if len(parts) > 1 else ""
    return head, body


def iter_request_files() -> Iterable[Path]:
    folder = Path(config.REQUESTS_DIR)
    folder.mkdir(parents=True, exist_ok=True)
    for path in sorted(folder.glob("*.txt")):
        # Ignore example files unless the user renames them.
        if path.name.lower().startswith("example"):
            continue
        yield path


def format_response_block(response: requests.Response) -> str:
    status_line = f"{response.status_code} {response.reason} {response.url}"
    headers = "\n".join(f"{k}: {v}" for k, v in response.headers.items())
    body = response.text
    return "\n".join(
        [
            "=" * 70,
            status_line,
            headers,
            "",
            body,
            "=" * 70,
            "",
        ]
    )


class ResponseSink:
    def __init__(self, target: Optional[str]) -> None:
        """
        target:
            None       -> disabled
            True/""    -> console dump
            "file"     -> append to responses/<file> (or absolute path)
        """
        if target is None:
            self.mode = "off"
            self.path = None
        elif target is True:
            self.mode = "console"
            self.path = None
        else:
            dest = Path(target)
            if not dest.is_absolute():
                dest = Path(config.RESPONSES_DIR) / dest
            dest.parent.mkdir(parents=True, exist_ok=True)
            self.mode = "file"
            self.path = dest

    def enabled(self) -> bool:
        return self.mode != "off"

    def write(self, response: requests.Response) -> None:
        block = format_response_block(response)
        if self.mode == "console":
            print(block)
        elif self.mode == "file" and self.path:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(block)


def send_request(
    parsed: ParsedRequest,
    session: requests.Session,
    proxies: Optional[Dict[str, str]] = None,
    verify_override: Optional[bool] = None,
) -> requests.Response:
    headers = {
        key: value
        for key, value in parsed.headers.items()
        if key.lower() not in config.SKIP_HEADERS
    }

    if parsed.path.lower().startswith(("http://", "https://")):
        url = parsed.path
    else:
        host = config.DEFAULT_HOST or parsed.headers.get("Host")
        if not host:
            raise ValueError("Host header is missing and DEFAULT_HOST is not set")
        url = f"{config.SCHEME}://{host}{parsed.path}"
    response = session.request(
        parsed.method,
        url,
        headers=headers,
        data=parsed.body,
        verify=config.VERIFY_TLS if verify_override is None else verify_override,
        timeout=config.TIMEOUT_SECONDS,
        proxies=proxies,
    )
    return response


def normalize_proxy_line(line: str) -> Optional[str]:
    raw = line.strip()
    if not raw or raw.startswith("#"):
        return None

    token = raw.split()[0]

    if "://" in token:
        return token
    if "@" in token:
        return f"http://{token}"

    parts = token.split(":")
    if len(parts) >= 4:
        host, port, user, password = parts[0], parts[1], parts[2], parts[3]
        return f"http://{user}:{password}@{host}:{port}"
    if len(parts) >= 2:
        host, port = parts[0], parts[1]
        return f"http://{host}:{port}"

    return f"http://{token}"


def load_proxies(path: Path) -> List[str]:
    if not path.exists():
        logging.warning("Proxy file not found: %s", path)
        return []

    proxies: List[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        normalized = normalize_proxy_line(raw_line)
        if normalized:
            proxies.append(normalized)
    return proxies


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


def send_with_proxy_failover(
    parsed: ParsedRequest, session: requests.Session, pool: ProxyPool
) -> requests.Response:
    while True:
        proxy_url = pool.next_proxy()
        if proxy_url is None and pool.exhausted():
            raise ProxyExhausted("Proxy list exhausted")
        proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
        tried_insecure = False
        while True:
            try:
                response = send_request(
                    parsed,
                    session,
                    proxies=proxies,
                    verify_override=False if tried_insecure else None,
                )
                if proxy_url and not response.ok:
                    logging.warning(
                        "Proxy %s returned HTTP %s; dropping and trying next.",
                        proxy_url,
                        response.status_code,
                    )
                    pool.mark_bad(proxy_url)
                    if pool.exhausted():
                        raise ProxyExhausted("Proxy list exhausted")
                    break  # go to next proxy
                mode = f"proxy={proxy_url}" if proxy_url else (
                    "direct-insecure" if tried_insecure else "direct"
                )
                if tried_insecure and not proxy_url:
                    logging.warning("SSL verification disabled for this request (direct).")
                logging.info(
                    "%s %s -> %s (%s bytes) via %s",
                    parsed.method,
                    parsed.path,
                    response.status_code,
                    len(response.content),
                    mode,
                )
                return response
            except requests.exceptions.SSLError as exc:
                if not tried_insecure:
                    logging.warning(
                        "SSL error via %s; retrying without verification.",
                        proxy_url or "direct",
                    )
                    tried_insecure = True
                    continue
                if proxy_url:
                    logging.error(
                        "Proxy failed (%s) after SSL retry, removing. Error: %s",
                        proxy_url,
                        exc,
                    )
                    pool.mark_bad(proxy_url)
                    if pool.exhausted():
                        raise ProxyExhausted("Proxy list exhausted")
                    break
                raise
            except requests.RequestException as exc:
                if proxy_url:
                    logging.error("Proxy failed (%s), removing. Error: %s", proxy_url, exc)
                    pool.mark_bad(proxy_url)
                    if pool.exhausted():
                        raise ProxyExhausted("Proxy list exhausted")
                    break
                raise


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
    return parser.parse_args()


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

            for path in files:
                try:
                    raw_text = path.read_text(encoding="utf-8")
                    raw_text = resolver.replace(raw_text)
                    parsed = parse_raw_request(raw_text)
                    response = send_with_proxy_failover(parsed, session, pool)
                    if response_sink.enabled():
                        response_sink.write(response)
                except Exception as exc:
                    logging.error("Failed to send %s: %s", path.name, exc)

            time.sleep(config.INTERVAL_SECONDS)
    except KeyboardInterrupt:
        logging.info("Interrupted with Ctrl+C, exiting cleanly.")
    except ProxyExhausted as exc:
        logging.error("%s. Terminating.", exc)
    finally:
        session.close()


def main() -> None:
    args = parse_args()
    if args.check:
        if args.direct:
            logging.warning("--check with --direct: nothing to test (no proxies).")
            return
        proxies = load_proxies(Path(args.proxy_file))
        check_proxies(proxies, dest_file=Path(args.proxy_file))
        return
    run_loop(args)


if __name__ == "__main__":
    main()
