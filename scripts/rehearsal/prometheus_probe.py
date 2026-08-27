#!/usr/bin/env python3
"""Parse the bounded Prometheus evidence used by deployment rehearsal step 13.

The shell harness fetches Prometheus' JSON APIs and passes each document here.
Keeping the verdict in a small standard-library program makes the real parser
directly testable: source-text assertions cannot prove that a JSON predicate
accepts firing/recovery and refuses absent or ambiguous evidence.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


class ProbeError(ValueError):
    """The API document cannot establish one unambiguous observation."""


@dataclass(frozen=True)
class RuleObservation:
    name: str
    state: str
    health: str
    active_alerts: int

    @property
    def coherent(self) -> bool:
        if self.state == "inactive":
            return self.active_alerts == 0
        if self.state in {"pending", "firing"}:
            return self.active_alerts > 0
        return False


@dataclass(frozen=True)
class TargetObservation:
    scrape_url: str
    health: str
    last_error: str


def _mapping(value: object, *, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ProbeError(f"{label} is not an object")
    return value


def _sequence(value: object, *, label: str) -> Sequence[Any]:
    if not isinstance(value, list):
        raise ProbeError(f"{label} is not a list")
    return value


def _successful_data(document: object) -> Mapping[str, Any]:
    root = _mapping(document, label="response")
    if root.get("status") != "success":
        raise ProbeError("response status is not success")
    return _mapping(root.get("data"), label="response.data")


def observe_rule(document: object, name: str) -> RuleObservation:
    data = _successful_data(document)
    groups = _sequence(data.get("groups"), label="response.data.groups")
    matches: list[Mapping[str, Any]] = []
    for group_index, raw_group in enumerate(groups):
        group = _mapping(raw_group, label=f"group[{group_index}]")
        rules = _sequence(group.get("rules"), label=f"group[{group_index}].rules")
        for rule_index, raw_rule in enumerate(rules):
            rule = _mapping(
                raw_rule,
                label=f"group[{group_index}].rules[{rule_index}]",
            )
            if rule.get("name") == name and rule.get("type") == "alerting":
                matches.append(rule)

    if len(matches) != 1:
        raise ProbeError(
            f"expected exactly one alerting rule named {name!r}, found {len(matches)}"
        )

    rule = matches[0]
    state = rule.get("state")
    health = rule.get("health")
    alerts = _sequence(rule.get("alerts"), label=f"rule {name!r}.alerts")
    if state not in {"inactive", "pending", "firing"}:
        raise ProbeError(f"rule {name!r} has unknown state {state!r}")
    if not isinstance(health, str):
        raise ProbeError(f"rule {name!r} has no health string")
    return RuleObservation(
        name=name,
        state=state,
        health=health,
        active_alerts=len(alerts),
    )


def observe_target(document: object, scrape_url: str) -> TargetObservation:
    data = _successful_data(document)
    targets = _sequence(data.get("activeTargets"), label="response.data.activeTargets")
    matches: list[Mapping[str, Any]] = []
    for target_index, raw_target in enumerate(targets):
        target = _mapping(raw_target, label=f"activeTargets[{target_index}]")
        if target.get("scrapeUrl") == scrape_url:
            matches.append(target)

    if len(matches) != 1:
        raise ProbeError(
            f"expected exactly one target {scrape_url!r}, found {len(matches)}"
        )

    target = matches[0]
    health = target.get("health")
    last_error = target.get("lastError", "")
    if health not in {"up", "down", "unknown"}:
        raise ProbeError(f"target {scrape_url!r} has unknown health {health!r}")
    if not isinstance(last_error, str):
        raise ProbeError(f"target {scrape_url!r} has no lastError string")
    return TargetObservation(
        scrape_url=scrape_url,
        health=health,
        last_error=last_error,
    )


def _bounded(value: str, limit: int = 200) -> str:
    single_line = value.replace("\r", " ").replace("\n", " ")
    return single_line if len(single_line) <= limit else single_line[:limit] + "..."


def _read_document() -> object:
    try:
        return json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ProbeError("response is not valid JSON") from exc


def _rule_summary(observation: RuleObservation) -> str:
    return (
        f"rule={observation.name} state={observation.state} "
        f"health={observation.health} active_alerts={observation.active_alerts}"
    )


def _target_summary(observation: TargetObservation) -> str:
    error = _bounded(observation.last_error) if observation.last_error else "none"
    return (
        f"target={observation.scrape_url} health={observation.health} "
        f"last_error={error}"
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    rule = subparsers.add_parser("rule-is")
    rule.add_argument("name")
    rule.add_argument("expected", choices=("inactive", "pending", "firing"))

    rule_summary = subparsers.add_parser("rule-summary")
    rule_summary.add_argument("name")

    target = subparsers.add_parser("target-is")
    target.add_argument("scrape_url")
    target.add_argument("expected", choices=("up", "down"))

    target_summary = subparsers.add_parser("target-summary")
    target_summary.add_argument("scrape_url")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        document = _read_document()
        if args.command.startswith("rule-"):
            observation = observe_rule(document, args.name)
            print(_rule_summary(observation))
            if args.command == "rule-summary":
                return 0
            return int(
                observation.state != args.expected
                or observation.health != "ok"
                or not observation.coherent
            )

        observation = observe_target(document, args.scrape_url)
        print(_target_summary(observation))
        if args.command == "target-summary":
            return 0
        if observation.health != args.expected:
            return 1
        if args.expected == "up" and observation.last_error:
            return 1
        return 0
    except ProbeError as exc:
        print(f"unproved: {_bounded(str(exc))}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
