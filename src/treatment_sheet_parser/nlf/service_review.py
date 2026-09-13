"""Interactive CLI decisions for invoice services absent from the map."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from treatment_sheet_parser.nlf.models import Service
from treatment_sheet_parser.nlf.service_mapping import (
    ServiceDecision,
    ServiceMappingError,
    ServiceReference,
)

OPTION_FIELDS = {
    "Cat.Services": "Services",
    "Cat.Additional Services": "Additional Services",
}


@dataclass(frozen=True)
class AirtableOption:
    """One selectable Airtable service option and its owning field."""

    value: str
    field: str


@dataclass(frozen=True)
class AirtableServiceOptions:
    """Validated service choices copied from the Airtable schema."""

    document: dict[str, list[str]]

    @property
    def choices(self) -> tuple[AirtableOption, ...]:
        """Flatten grouped schema options into stable, globally numbered choices.

        Document field order and each field's option order are preserved so the
        displayed number always resolves to the corresponding option.
        """
        return tuple(
            AirtableOption(value, OPTION_FIELDS[key])
            for key, values in self.document.items()
            for value in values
        )


class InteractiveServiceDecider:
    """Prompt for report-and-ignore or a confirmed Airtable option map."""

    def __init__(self, options: AirtableServiceOptions) -> None:
        """Configure interactive review with a validated schema snapshot.

        Args:
            options: Airtable service options that may be selected for new maps.
        """
        self.options = options

    def __call__(self, service: Service, _reference: ServiceReference) -> ServiceDecision:
        """Prompt for and return a confirmed unknown-service decision.

        Ignoring is explicit. Mapping requires both selection of a displayed
        Airtable option and a second confirmation; declining aborts the mapping.
        """
        print(f"Unknown invoice service: {service.name!r} (cost {service.cost!r})")
        action = _prompt_choice("Choose [i] report and ignore or [m]ap: ", {"i", "m"})
        if action == "i":
            print("Reported and ignored; its cost remains in invoice total checks.")
            return ServiceDecision("ignore")
        selected = _prompt_option(self.options)
        if not _confirm(service, selected):
            return ServiceDecision("abort")
        return ServiceDecision("map_existing", selected.value, selected.field)


def prompt_odd_cost(service: Service) -> Literal["error", "skip"]:
    """Ask how to handle a service whose printed cost is not numeric.

    Returns ``skip`` only for an explicit ``s`` response; ``e`` requests the
    normal mapping error. Input is retried until either choice is entered.
    """
    print(f"Service {service.name!r} has non-numeric cost {service.cost!r}")
    choice = _prompt_choice("Choose [e]rror or [s]kip/ignore: ", {"e", "s"})
    return "skip" if choice == "s" else "error"


def load_airtable_service_options(path: str | Path) -> AirtableServiceOptions:
    """Load a complete, validated snapshot of Airtable service options.

    Args:
        path: JSON file containing exactly the supported schema field keys.

    Raises:
        ServiceMappingError: If the file cannot be decoded or its fields and
            options do not satisfy the expected schema.
    """
    options_path = Path(path).expanduser().resolve()
    try:
        document = json.loads(options_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        message = f"could not read Airtable service options {options_path}: {exc}"
        raise ServiceMappingError(message) from exc
    _validate_options(document)
    return AirtableServiceOptions(cast(dict[str, list[str]], document))


def _validate_options(document: object) -> None:
    """Validate the service-option document's fields and option uniqueness.

    Exactly the supported field keys are required, and option names must be
    unique across fields under case-insensitive comparison.
    """
    if not isinstance(document, dict) or set(document) != set(OPTION_FIELDS):
        expected = ", ".join(OPTION_FIELDS)
        raise ServiceMappingError(f"Airtable service options must contain exactly: {expected}")
    values = [value for options in document.values() for value in _option_list(options)]
    if len({value.casefold() for value in values}) != len(values):
        raise ServiceMappingError("Airtable service options must be unique across fields")


def _option_list(value: object) -> list[str]:
    """Return a schema option list after validating its shape and contents.

    Empty lists and blank or non-string options are rejected because they cannot
    be selected or safely persisted in a mapping.
    """
    if not isinstance(value, list) or not value:
        raise ServiceMappingError("each Airtable service field must have a non-empty option array")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise ServiceMappingError("Airtable service options must be non-empty strings")
    return cast(list[str], value)


def _prompt_option(options: AirtableServiceOptions) -> AirtableOption:
    """Prompt until the operator selects a displayed Airtable option.

    Selection accepts either a one-based global number or a case-insensitive
    exact option name; ambiguous partial names are never accepted.
    """
    choices = options.choices
    _print_options(options)
    while True:
        entered = input("Service number or name: ").strip()
        selected = _selected_option(entered, choices)
        if selected is not None:
            return selected
        print("Enter a displayed service number or name.")


def _print_options(options: AirtableServiceOptions) -> None:
    """Print options grouped by schema field with global one-based numbering.

    The numbering follows ``AirtableServiceOptions.choices`` exactly.
    """
    index = 1
    for key, values in options.document.items():
        print(f"{key}:")
        for value in values:
            print(f"  {index}. {value}")
            index += 1


def _selected_option(entered: str, choices: tuple[AirtableOption, ...]) -> AirtableOption | None:
    """Resolve operator text to one option, or return ``None`` if invalid.

    Numeric input is interpreted as a one-based index; other input must equal a
    complete option name under case-insensitive comparison.
    """
    if entered.isdigit() and 1 <= int(entered) <= len(choices):
        return choices[int(entered) - 1]
    normalized = entered.casefold()
    return next((choice for choice in choices if choice.value.casefold() == normalized), None)


def _confirm(service: Service, selected: AirtableOption) -> bool:
    """Display a proposed service map and return its explicit confirmation.

    No reference is changed here; the caller converts the boolean result into a
    mapping or abort decision.
    """
    print(f"Map invoice service {service.name!r} to {selected.field}.{selected.value!r}.")
    prompt = "Is this the map you wanted to add to the service map reference? [y/n]: "
    return _prompt_choice(prompt, {"y", "n"}) == "y"


def _prompt_choice(prompt: str, choices: set[str]) -> str:
    """Read normalized operator input until it belongs to ``choices``.

    Invalid responses print the allowed choices and retry. The returned value is
    stripped and case-folded.
    """
    while True:
        value = input(prompt).strip().casefold()
        if value in choices:
            return value
        print(f"Enter one of: {', '.join(sorted(choices))}.")
