# Kernel migration configuration source inventory

This bounded extension extracts URL selection and Alembic ConfigParser writing
into the existing Kernel migration boundary. It introduces no database runtime,
settings resolver, authority check, migration lineage, or deployment owner.

| Product | Immutable source | Finding |
|---|---|---|
| Sub | `935125cafbf0b395b0c6bed1e4a49b13968f924c`, `app/config.py::Settings.alembic_database_url`, `alembic/env.py` | Qualifying existing selector: an absent migration owner permits the product's explicit runtime fallback; a supplied blank owner refuses; a present owner is returned unchanged. Its adapter escapes percent signs only at `Config.set_main_option`. These are existing product implementation bytes, not a production adoption claim. |
| ERP | `93d7e111d9278b8377bac311e78308c99f51da79`, `alembic/env.py::_migration_url` and the `set_main_option` call | ERP requires a dedicated migration URL and has additional database authority verification. It qualifies for the ConfigParser write seam, but its strict owner policy must stay in ERP: the shared selector supports omitting fallback. Its trimming is deliberately not transferred; held URL bytes stay unchanged. |
| Starter | `69609b67356025084f768174c8c86b12c8db0601`, `alembic/env.py::get_url` | Reference adapter duplicates environment selection with `or`, allowing a blank migration owner to use the runtime URL. The local selector retires in this change and the assembly explicitly supplies its established DATABASE_URL fallback. |

Kernel owns the pure selector and percent-safe write helper. Products retain
material loading, fallback permission, both database planes, engine and session
construction, prerequisite bindings, schema/lineage composition, and runtime
transaction authority. Neither helper reads settings or environment. The existing
Kernel package root still reads its established settings surface; this change
makes no new claim that importing the entire package is settings-free.

Hosted tests in `tests/unit/test_migration_configuration.py` cover owner
precedence, unchanged URL bytes, blank/missing refusal, a real Alembic Config
percent round-trip, and the reference adapter's online/offline delegation.
`tests/architecture/test_migration_configuration_is_pure.py` guards the bounded
module's import dependencies and checks import-time engine/session construction
in a fresh interpreter, including a planted violation. Tests run only on
Git-hosted CI. Source changes remain unreleased until the existing governed
candidate/publication process supplies an installed artifact; no version or
product cutover is allocated by this inventory.
