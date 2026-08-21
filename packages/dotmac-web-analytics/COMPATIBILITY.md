# Compatibility contract

The stable public surface is exactly `dotmac_web_analytics.__all__`.
Submodules are implementation details.

Protocol compatibility is explicit:

- collection commands carry a positive `protocol_version`;
- product event declarations carry `(code, schema_version)`;
- classification evidence carries `(classifier_code, classifier_version)`;
- session rules, funnel definitions and projection generations are versioned;
- unknown versions fail closed rather than being guessed.

Patch releases may add optional result fields and new typed refusal codes. A
change that reinterprets an accepted command, event declaration, visitor
pseudonym, session boundary, aggregate dimension, funnel or retention action is
major unless a new explicit version leaves the earlier contract reproducible.

Website hostnames, routes, origins, property/stream codes, event vocabularies,
consent modes and retention periods are adopter configuration and never part of
the package compatibility surface.
