#!/usr/bin/env python3
"""Configure and call the GPT Image 2 API used by this personal skill."""

from __future__ import annotations

import argparse
import base64
import ctypes
import getpass
import json
import mimetypes
import os
import secrets
import stat
import sys
import urllib.error
import urllib.parse
import urllib.request
from ctypes import wintypes
from datetime import datetime
from pathlib import Path
from typing import Any


SKILL_DIR = Path(__file__).resolve().parents[1]
CONFIG_PATH = Path(
    os.environ.get("GPT_IMAGE_2_SKILL_CONFIG", SKILL_DIR / "config.local.json")
)
DEFAULT_CONFIG = {
    "base_url": "https://www.e0hub.com",
    "model": "gpt-image-2",
    "generation_endpoint": "/v1/images/generations",
    "edit_endpoint": "/v1/images/edits",
    "response_format": "url",
}
SUPPORTED_SIZES = (
    "1024x1024",
    "1536x1024",
    "1024x1536",
    "2048x2048",
    "2048x1152",
    "3840x2160",
    "2160x3840",
    "auto",
)
SUPPORTED_REFERENCE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")


class DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_byte)),
    ]


if os.name == "nt":
    _crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(DataBlob),
        wintypes.LPCWSTR,
        ctypes.POINTER(DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(DataBlob),
    ]
    _crypt32.CryptProtectData.restype = wintypes.BOOL
    _crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(DataBlob),
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(DataBlob),
    ]
    _crypt32.CryptUnprotectData.restype = wintypes.BOOL
    _kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    _kernel32.LocalFree.restype = ctypes.c_void_p


def _windows_protect(value: str) -> str:
    raw = value.encode("utf-8")
    buffer = ctypes.create_string_buffer(raw)
    source = DataBlob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    target = DataBlob()
    ok = _crypt32.CryptProtectData(
        ctypes.byref(source),
        "GPT Image 2 skill key",
        None,
        None,
        None,
        0,
        ctypes.byref(target),
    )
    if not ok:
        raise ctypes.WinError()
    try:
        protected = ctypes.string_at(target.pbData, target.cbData)
        return base64.b64encode(protected).decode("ascii")
    finally:
        _kernel32.LocalFree(ctypes.cast(target.pbData, ctypes.c_void_p))


def _windows_unprotect(value: str) -> str:
    raw = base64.b64decode(value)
    buffer = ctypes.create_string_buffer(raw)
    source = DataBlob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    target = DataBlob()
    ok = _crypt32.CryptUnprotectData(
        ctypes.byref(source), None, None, None, None, 0, ctypes.byref(target)
    )
    if not ok:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(target.pbData, target.cbData).decode("utf-8")
    finally:
        _kernel32.LocalFree(ctypes.cast(target.pbData, ctypes.c_void_p))


def load_config() -> dict[str, Any]:
    config = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        saved = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        if not isinstance(saved, dict):
            raise RuntimeError("Skill configuration must be a JSON object.")
        config.update(saved)
    return config


def get_api_key(config: dict[str, Any]) -> str | None:
    if config.get("api_key_dpapi"):
        if os.name != "nt":
            raise RuntimeError("This API key is protected for a Windows account.")
        return _windows_unprotect(str(config["api_key_dpapi"]))
    if config.get("api_key"):
        return str(config["api_key"])
    return None


def save_api_key(api_key: str) -> None:
    api_key = api_key.strip()
    if not api_key:
        raise ValueError("API key cannot be empty.")

    config = load_config()
    config.pop("api_key", None)
    config.pop("api_key_dpapi", None)
    if os.name == "nt":
        config["api_key_dpapi"] = _windows_protect(api_key)
        config["key_storage"] = "windows-dpapi"
    else:
        config["api_key"] = api_key
        config["key_storage"] = "local-file"

    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = CONFIG_PATH.with_suffix(CONFIG_PATH.suffix + ".tmp")
    temporary_path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.chmod(temporary_path, stat.S_IRUSR | stat.S_IWUSR)
    temporary_path.replace(CONFIG_PATH)
    os.chmod(CONFIG_PATH, stat.S_IRUSR | stat.S_IWUSR)


def clear_api_key() -> None:
    if not CONFIG_PATH.exists():
        return
    config = load_config()
    config.pop("api_key", None)
    config.pop("api_key_dpapi", None)
    config.pop("key_storage", None)
    CONFIG_PATH.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.chmod(CONFIG_PATH, stat.S_IRUSR | stat.S_IWUSR)


def endpoint_url(base_url: str, endpoint: str) -> str:
    return f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}"


def build_multipart(
    fields: list[tuple[str, str]], files: list[tuple[str, Path]]
) -> tuple[bytes, str]:
    boundary = f"----gpt-image-2-{secrets.token_hex(16)}"
    body = bytearray()

    def add_line(value: bytes = b"") -> None:
        body.extend(value + b"\r\n")

    for name, value in fields:
        add_line(f"--{boundary}".encode("ascii"))
        add_line(f'Content-Disposition: form-data; name="{name}"'.encode("utf-8"))
        add_line()
        add_line(value.encode("utf-8"))

    for name, path in files:
        mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        safe_name = path.name.replace('"', "_")
        add_line(f"--{boundary}".encode("ascii"))
        add_line(
            f'Content-Disposition: form-data; name="{name}"; filename="{safe_name}"'.encode(
                "utf-8"
            )
        )
        add_line(f"Content-Type: {mime_type}".encode("ascii"))
        add_line()
        body.extend(path.read_bytes())
        add_line()

    add_line(f"--{boundary}--".encode("ascii"))
    return bytes(body), f"multipart/form-data; boundary={boundary}"


def api_request(url: str, data: bytes, headers: dict[str, str]) -> dict[str, Any]:
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            payload = response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        try:
            decoded = json.loads(detail)
            detail = (
                decoded.get("error", {}).get("message")
                or decoded.get("message")
                or detail
            )
        except (json.JSONDecodeError, AttributeError):
            pass
        raise RuntimeError(f"API returned HTTP {error.code}: {detail}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"Unable to reach the image API: {error.reason}") from error

    try:
        decoded = json.loads(payload)
    except json.JSONDecodeError as error:
        raise RuntimeError("The image API returned invalid JSON.") from error
    if not isinstance(decoded, dict):
        raise RuntimeError("The image API returned an unexpected response.")
    return decoded


def download_image(url: str, authorization: str | None = None) -> bytes:
    headers = {"User-Agent": "e0hub-image-skill/1.0"}
    if authorization:
        headers["Authorization"] = authorization
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=120) as image_response:
        return image_response.read()


def save_results(
    response: dict[str, Any],
    output_dir: Path,
    output_format: str,
    authorization: str | None = None,
    api_base_url: str | None = None,
) -> list[dict[str, Any]]:
    data = response.get("data")
    if not isinstance(data, list):
        raise RuntimeError("The image API response does not contain a data array.")

    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    results: list[dict[str, Any]] = []
    for index, item in enumerate(data, start=1):
        if not isinstance(item, dict):
            continue
        path = (output_dir / f"gpt-image-2-{timestamp}-{index}.{output_format}").resolve()
        result: dict[str, Any] = {
            "path": str(path),
            "revised_prompt": item.get("revised_prompt"),
        }
        if item.get("b64_json"):
            path.write_bytes(base64.b64decode(item["b64_json"]))
        elif item.get("url"):
            result["url"] = item["url"]
            try:
                path.write_bytes(download_image(item["url"]))
            except (urllib.error.URLError, OSError) as error:
                try:
                    host = urllib.parse.urlparse(item["url"]).hostname
                    api_host = urllib.parse.urlparse(api_base_url or "").hostname
                    if host != api_host:
                        raise error
                    path.write_bytes(download_image(item["url"], authorization))
                except (urllib.error.URLError, OSError):
                    result["path"] = None
                    result["download_error"] = str(error)
        else:
            result["path"] = None
            result["error"] = "No b64_json or url was returned for this image."
        results.append(result)
    return results


def generate(args: argparse.Namespace) -> int:
    config = load_config()
    api_key = get_api_key(config)
    if not api_key and not args.dry_run:
        raise RuntimeError("API key is not configured. Run the configure command first.")

    references = [Path(item).expanduser().resolve() for item in args.reference]
    missing = [str(path) for path in references if not path.is_file()]
    if missing:
        raise ValueError(f"Reference image not found: {missing[0]}")
    unsupported = [
        str(path) for path in references if path.suffix.lower() not in SUPPORTED_REFERENCE_SUFFIXES
    ]
    if unsupported:
        raise ValueError(
            f"Unsupported reference image format: {unsupported[0]}. "
            "Use PNG, JPEG, or WEBP."
        )

    common_fields = [
        ("model", str(config["model"])),
        ("prompt", args.prompt),
        ("n", str(args.count)),
        ("size", args.size),
        ("quality", args.quality),
        ("background", args.background),
        ("output_format", args.output_format),
        ("response_format", str(config["response_format"])),
    ]

    if args.dry_run:
        print(
            json.dumps(
                {
                    "mode": "edit" if references else "generation",
                    "model": config["model"],
                    "reference_count": len(references),
                    "size": args.size,
                    "quality": args.quality,
                    "output_format": args.output_format,
                    "count": args.count,
                },
                ensure_ascii=False,
            )
        )
        return 0

    headers = {
        "Authorization": f"Bearer {api_key}",
        "User-Agent": "e0hub-image-skill/1.0",
    }
    if references:
        body, content_type = build_multipart(
            common_fields, [("image[]", path) for path in references]
        )
        headers["Content-Type"] = content_type
        url = endpoint_url(str(config["base_url"]), str(config["edit_endpoint"]))
    else:
        # The JSON endpoint expects `n` as a number; form-data fields remain strings.
        payload = {
            "model": str(config["model"]),
            "prompt": args.prompt,
            "n": args.count,
            "size": args.size,
            "quality": args.quality,
            "background": args.background,
            "output_format": args.output_format,
            "response_format": str(config["response_format"]),
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
        url = endpoint_url(
            str(config["base_url"]), str(config["generation_endpoint"])
        )

    response = api_request(url, body, headers)
    results = save_results(
        response,
        Path(args.output_dir).expanduser(),
        args.output_format,
        headers["Authorization"],
        str(config["base_url"]),
    )
    print(json.dumps({"results": results}, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("config-status", help="Report whether an API key is saved.")
    subparsers.add_parser("configure", help="Securely prompt for and save an API key.")
    subparsers.add_parser("clear-key", help="Remove the saved API key.")

    generate_parser = subparsers.add_parser("generate", help="Generate or edit images.")
    generate_parser.add_argument("--prompt", required=True)
    generate_parser.add_argument("--reference", action="append", default=[])
    generate_parser.add_argument("--size", choices=SUPPORTED_SIZES, default="1024x1024")
    generate_parser.add_argument(
        "--quality", choices=("auto", "low", "medium", "high"), default="auto"
    )
    generate_parser.add_argument(
        "--background", choices=("auto", "transparent", "opaque"), default="auto"
    )
    generate_parser.add_argument(
        "--output-format", choices=("png", "jpeg", "webp"), default="png"
    )
    generate_parser.add_argument("--count", type=int, choices=range(1, 5), default=1)
    generate_parser.add_argument("--output-dir", default="generated-images")
    generate_parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "config-status":
            config = load_config()
            print(
                json.dumps(
                    {
                        "configured": bool(get_api_key(config)),
                        "base_url": config["base_url"],
                        "model": config["model"],
                        "key_storage": config.get("key_storage"),
                    },
                    ensure_ascii=False,
                )
            )
            return 0
        if args.command == "configure":
            prompt = "API key: "
            api_key = getpass.getpass(prompt) if sys.stdin.isatty() else sys.stdin.readline()
            save_api_key(api_key)
            print("API key saved in the skill's local configuration.")
            return 0
        if args.command == "clear-key":
            clear_api_key()
            print("Saved API key removed.")
            return 0
        if args.command == "generate":
            return generate(args)
        return 2
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
