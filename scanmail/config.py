"""Strict, human-editable YAML configuration."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import re
from email.utils import parseaddr

import yaml


@dataclass(frozen=True)
class Polling:
    interval_seconds: int
    min_file_age_seconds: int
    stable_observations: int
    max_attachment_bytes: int
    extensions: frozenset[str]


@dataclass(frozen=True)
class SMTP:
    host: str
    port: int
    security: str
    username: str
    password: str
    from_address: str
    from_name: str
    timeout_seconds: int


@dataclass(frozen=True)
class Route:
    id: str
    inbox: Path
    archive: Path
    to: tuple[str, ...]
    subject: str | None


@dataclass(frozen=True)
class Config:
    polling: Polling
    smtp: SMTP
    subject: str
    body: str
    routes: tuple[Route, ...]


def _obj(value: object, name: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{name}: expected a YAML mapping")
    return value


def _string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name}: expected a nonempty string")
    return value.strip()


def _integer(value: object, name: str, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name}: expected an integer >= {minimum}")
    return value


def _address(value: object, name: str) -> str:
    address = _string(value, name)
    realname, parsed = parseaddr(address)
    if (realname or parsed != address or "\n" in address or "\r" in address
            or address.count("@") != 1 or " " in address):
        raise ValueError(f"{name}: provide an email address only (without a display name)")
    local, domain = address.rsplit("@", 1)
    if not local or "." not in domain or not domain.rsplit(".", 1)[-1]:
        raise ValueError(f"{name}: invalid email address: {address}")
    return address


def _template(value: object, name: str) -> str:
    template = _string(value, name)
    try:
        template.format(route="konto1", filename="test.pdf")
    except (KeyError, ValueError, IndexError, AttributeError) as error:
        raise ValueError(f"{name}: allowed variables are {{route}} and {{filename}}: {error}") from error
    if "\n" in template and name.endswith("subject"):
        raise ValueError(f"{name}: subject must not contain newlines")
    return template


def _path(value: object, name: str) -> Path:
    path = Path(_string(value, name))
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{name}: expected an absolute path without '..'")
    return path


def load_config(config_path: Path) -> Config:
    with config_path.open("r", encoding="utf-8") as handle:
        root = _obj(yaml.safe_load(handle), "configuration")
    if root.get("version") != 1:
        raise ValueError("Supported configuration version: version: 1")
    poll = _obj(root.get("polling"), "polling")
    extensions = poll.get("extensions")
    if not isinstance(extensions, list) or not extensions:
        raise ValueError("polling.extensions: expected a list of extensions")
    ext_items = []
    for item in extensions:
        item = _string(item, "polling.extensions[]").lower()
        if not re.fullmatch(r"\.[a-z0-9]+", item):
            raise ValueError(f"Invalid extension: {item}")
        ext_items.append(item)
    polling = Polling(
        interval_seconds=_integer(poll.get("interval_seconds"), "polling.interval_seconds"),
        min_file_age_seconds=_integer(poll.get("min_file_age_seconds"), "polling.min_file_age_seconds", 0),
        stable_observations=_integer(poll.get("stable_observations"), "polling.stable_observations"),
        max_attachment_bytes=_integer(poll.get("max_attachment_mb"), "polling.max_attachment_mb") * 1024 * 1024,
        extensions=frozenset(ext_items),
    )

    smtp_obj = _obj(root.get("smtp"), "smtp")
    encryption = _string(smtp_obj.get("security"), "smtp.security").lower()
    if encryption not in {"ssl", "starttls"}:
        raise ValueError("smtp.security must be 'ssl' or 'starttls'")
    username_env = _string(smtp_obj.get("username_env"), "smtp.username_env")
    password_env = _string(smtp_obj.get("password_env"), "smtp.password_env")
    username = os.environ.get(username_env, "").strip()
    password = os.environ.get(password_env, "")
    if not username or not password:
        raise ValueError(f"Missing environment variables: {username_env} / {password_env}")
    smtp = SMTP(
        host=_string(smtp_obj.get("host"), "smtp.host"),
        port=_integer(smtp_obj.get("port"), "smtp.port"),
        security=encryption,
        username=username,
        password=password,
        from_address=_address(smtp_obj.get("from_address"), "smtp.from_address"),
        from_name=_string(smtp_obj.get("from_name", "Scanner"), "smtp.from_name"),
        timeout_seconds=_integer(smtp_obj.get("timeout_seconds", 30), "smtp.timeout_seconds"),
    )

    message = _obj(root.get("message"), "message")
    subject = _template(message.get("subject"), "message.subject")
    body = _template(message.get("body"), "message.body")
    routes_yaml = root.get("routes")
    if not isinstance(routes_yaml, list) or not routes_yaml:
        raise ValueError("routes: expected a list with at least one route")

    routes = []
    ids = set()
    used_paths = set()
    for i, item in enumerate(routes_yaml):
        obj = _obj(item, f"routes[{i}]")
        route_id = _string(obj.get("id"), f"routes[{i}].id")
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", route_id) or route_id in ids:
            raise ValueError(f"Invalid or duplicate route.id: {route_id}")
        ids.add(route_id)
        inbox = _path(obj.get("inbox"), f"routes[{i}].inbox")
        archive = _path(obj.get("archive"), f"routes[{i}].archive")
        if inbox == archive:
            raise ValueError(f"{route_id}: inbox must not be the same as archive")
        if inbox in archive.parents or archive in inbox.parents:
            raise ValueError(f"{route_id}: inbox/archive directories must not be nested")
        if inbox in used_paths or archive in used_paths:
            raise ValueError(f"{route_id}: directory already used by another route")
        used_paths.update((inbox, archive))
        recipients = obj.get("to")
        if not isinstance(recipients, list) or not recipients:
            raise ValueError(f"{route_id}.to: expected a list of recipients")
        to = tuple(_address(item, f"{route_id}.to") for item in recipients)
        subject_override = obj.get("subject")
        routes.append(Route(route_id, inbox, archive, to,
                            _template(subject_override, f"routes[{i}].subject") if subject_override is not None else None))
    return Config(polling, smtp, subject, body, tuple(routes))
