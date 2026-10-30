"""Hosted canaries for bounded lexical callable demand (no source execution)."""

import copy

import pytest

from tools.composition_contract import compatibility_gate as gate

LOADER = (
    "from importlib import import_module\ndef load(name):\n    import_module(name)\n"
)


def _facts(sources):
    return {
        module: gate._callable_facts_from_source(
            source.encode(),
            current_module=module,
            is_package=module == "pkg",
            available_modules=frozenset(sources),
        )
        for module, source in sources.items()
    }


def _check(product, *, loader=LOADER, exports="from pkg.loader import load\n"):
    facts = _facts({"app": product, "pkg": exports, "pkg.loader": loader})
    gate._check_callable_demand(facts, production_modules=frozenset({"app"}))
    return facts


def test_unused_reexported_loader_is_discharged_only_after_closure():
    _check("import pkg\n")


@pytest.mark.parametrize(
    "product",
    [
        "from pkg import load\nload('unknown')\n",
        "import pkg as p\np.load('unknown')\n",
        "from pkg import load\nalias = load\nalias('unknown')\n",
        "from pkg import load\ndef wrapper():\n    load('unknown')\n",
        "from pkg import load\nregister(load)\n",
        "import pkg\nregister(pkg)\n",
        "import pkg\ngetattr(pkg, field)('unknown')\n",
    ],
)
def test_calls_aliases_callbacks_and_namespace_escapes_refuse(product):
    with pytest.raises(gate.GateDerivationError, match="dynamic import"):
        _check(product)


def test_module_suite_obligation_is_always_demanded():
    with pytest.raises(gate.GateDerivationError, match="dynamic import"):
        _check(
            "import pkg\n",
            loader="from importlib import import_module\nimport_module(name)\n",
        )


def test_invoked_environment_loader_remains_red():
    with pytest.raises(gate.GateDerivationError, match="dynamic import"):
        _check(
            "from pkg import load\nload()\n",
            loader=(
                "import os\nfrom importlib import import_module\n"
                "def load():\n    import_module(os.getenv('MODULE'))\n"
            ),
        )


def test_literal_arguments_do_not_cross_lexical_scopes_as_nonuse_proof():
    with pytest.raises(gate.GateDerivationError, match="dynamic import"):
        _check(
            "from pkg import load\nload('safe')\n",
            loader=(
                "from importlib import import_module\nname = 'safe'\n"
                "def load(name):\n    import_module(name)\n"
            ),
        )


def test_rebinding_retains_every_possible_callable_target():
    with pytest.raises(gate.GateDerivationError, match="dynamic import"):
        _check("from pkg import load\nalias = load\nalias = other\nalias()\n")


def test_local_shadow_does_not_resolve_to_foreign_loader():
    _check("from pkg import load\ndef f(load):\n    load()\n")


def test_used_class_demands_methods_but_definition_only_does_not():
    loader = (
        "from importlib import import_module\nclass Loader:\n"
        "    def load(self, name):\n        import_module(name)\n"
    )
    _check("import pkg\n", loader=loader, exports="from pkg.loader import Loader\n")
    with pytest.raises(gate.GateDerivationError, match="dynamic import"):
        _check(
            "from pkg import Loader\nLoader()\n",
            loader=loader,
            exports="from pkg.loader import Loader\n",
        )


@pytest.mark.parametrize(
    "effect",
    [
        "def f(value=load('x')):\n    pass\n",
        "@load('x')\ndef f():\n    pass\n",
        "class C:\n    value = load('x')\n",
    ],
)
def test_definition_execution_effects_are_retained(effect):
    with pytest.raises(gate.GateDerivationError, match="dynamic import"):
        _check("import pkg\n", exports="from pkg.loader import load\n" + effect)


def test_static_imports_and_resolved_dynamic_edges_survive_unused_functions():
    facts = _check(
        "import pkg\n",
        loader=(
            "from importlib import import_module\n"
            "def load(name):\n    from dotmac_kernel.db import SessionLocal\n"
            "    import_module('pkg.other')\n    import_module(name)\n"
        ),
    )
    assert {"dotmac_kernel.db", "pkg.other"} <= set(facts["pkg.loader"]["imports"])


@pytest.mark.parametrize(
    "mutation",
    [
        lambda f: f.update(extra=True),
        lambda f: f["scopes"][0].update(extra=True),
        lambda f: f["scopes"].append(copy.deepcopy(f["scopes"][0])),
        lambda f: f["scopes"][0].update(parent="missing"),
        lambda f: f["scopes"][0].update(uses=[[1]]),
        lambda f: f["scopes"][0].update(obligations=[None]),
        lambda f: f["scopes"][0].update(uses=[["x"]] * 10001),
    ],
)
def test_malformed_or_unbounded_lexical_facts_are_refused(mutation):
    facts = _facts({"app": "pass\n"})["app"]
    mutation(facts)
    with pytest.raises(gate.GateAcquisitionError):
        gate._validate_callable_facts(facts)


def test_importer_rebinding_does_not_hide_other_callable_targets():
    with pytest.raises(gate.GateDerivationError, match="dynamic import"):
        _check(
            "from pkg import load\nload('pkg')\n",
            loader=(
                "from importlib import import_module\n"
                "def load(name):\n    import_module(unknown)\n"
                "if flag:\n    load = import_module\n"
            ),
        )


def test_dynamic_module_result_cannot_hide_exported_loader_use():
    with pytest.raises(gate.GateDerivationError, match="dynamic import"):
        _check(
            "from importlib import import_module\n"
            "mod = import_module('pkg')\nmod.load('x')\n"
        )


def test_missing_symbol_code_is_not_nonuse_evidence():
    with pytest.raises(gate.GateDerivationError, match="missing code"):
        _check("from pkg import missing\nmissing()\n")


def test_reflection_and_star_import_cannot_discharge_unused_obligation():
    for product in ("from pkg import *\n", "import pkg\nnamespace = globals()\n"):
        with pytest.raises(gate.GateDerivationError, match="cannot discharge"):
            _check(product)


def test_foreign_v2_protocol_returns_closed_facts_and_v1_stays_strict():
    import sys

    arguments = {
        "executable": sys.executable,
        "product": "test",
        "revision": "a" * 40,
        "interpreter": "test-python",
        "module": "pkg.loader",
        "path": "pkg/loader.py",
        "is_package": False,
        "available_modules": frozenset({"pkg.loader"}),
    }
    facts = gate._imports_under_interpreter(
        LOADER.encode(), **arguments, callable_facts=True
    )
    assert isinstance(facts, dict)
    gate._check_callable_demand({"pkg.loader": facts}, production_modules=frozenset())
    with pytest.raises(gate.GateDerivationError, match="dynamic import"):
        gate._imports_under_interpreter(LOADER.encode(), **arguments)


def test_v2_counts_deferred_debt_in_unused_callable():
    facts = _facts(
        {"pkg": "def unused():\n    from dotmac_kernel.db import conflict_savepoint\n"}
    )
    assert facts["pkg"]["deferred_sites"] == 1
    assert "dotmac_kernel.db" in facts["pkg"]["imports"]


@pytest.mark.parametrize("invoke", [False, True])
def test_product_and_published_closure_use_v2_and_preserve_debt(
    tmp_path, monkeypatch, invoke
):
    import sys

    from tests.architecture.test_compatibility_gate import _git, _repo

    prefix = "packages/dotmac-files/src/dotmac_files"
    repo = _repo(
        tmp_path,
        {
            "app/__init__.py": "",
            "app/main.py": "from dotmac_files import load\n"
            + ("load('x')\n" if invoke else ""),
            f"{prefix}/__init__.py": "from .loader import load\n",
            f"{prefix}/loader.py": LOADER
            + (
                "def untouched():\n"
                "    from dotmac_kernel.db import conflict_savepoint\n"
            ),
        },
    )
    revision = _git(repo, "rev-parse", "HEAD")
    source = gate.PublishedSource("dotmac-files", "1.0", "dotmac-files-v1.0", revision)
    catalogue = gate._TrustedCatalogue(
        repo / "packages", {"dotmac-files": "dotmac_files"}
    )
    index, _ = gate._published_python_index(repo, catalogue, {"dotmac-files": source})
    selected = gate._SelectedInterpreter(
        "hosted-python", sys.executable, tuple(sys.version_info[:3])
    )
    monkeypatch.setattr(gate, "_select_interpreter", lambda *args, **kwargs: selected)
    reached = gate._product_runtime_reachability(
        product="academy",
        clone=repo,
        revision=revision,
        observations={},
        pyproject={"project": {"requires-python": ">=3.11"}},
        published_modules=frozenset(index),
    )
    assert reached.callable_facts is not None
    arguments = {
        "repository_root": repo,
        "records": (),
        "product_external_modules": reached.external_modules,
        "catalogue": catalogue,
        "sources": {"dotmac-files": source},
        "product_reachability": reached,
    }
    if invoke:
        with pytest.raises(gate.GateDerivationError, match="dynamic import"):
            gate._reached_deferred_debt(**arguments)
    else:
        debt = gate._reached_deferred_debt(**arguments)
        assert [(item.source_path, item.sites) for item in debt] == [
            (f"{prefix}/loader.py", 1)
        ]


@pytest.mark.parametrize(
    "product",
    [
        "import pkg\ng = globals\nnamespace = g()\n",
        "import pkg\nimport sys\nsys.modules['pkg'].load('x')\n",
        "__builtins__['__import__'](unknown)\n",
        "e = eval\ne(unknown)\n",
    ],
)
def test_reflected_namespaces_and_builtin_aliases_are_not_nonuse_proof(product):
    with pytest.raises(gate.GateDerivationError, match="dynamic import"):
        _check(product)


@pytest.mark.parametrize(
    "plant", ["missing", "extra", "mismatch", "bad_scope", "wrong_version"]
)
def test_v2_protocol_rejects_malformed_facts_at_boundary(monkeypatch, plant):
    import json
    from types import SimpleNamespace

    def fake_run(_args, *, input, **kwargs):
        request = json.loads(input)
        response = {
            key: request[key]
            for key in (
                "schema_version",
                "product",
                "revision",
                "interpreter",
                "module",
                "path",
            )
        }
        facts = _facts({"app": "import dotmac_files\n"})["app"]
        response.update(
            status="ok", error=None, imports=facts["imports"], callable_facts=facts
        )
        if plant == "missing":
            response.pop("callable_facts")
        elif plant == "extra":
            facts["ast"] = {"type": "Module"}
        elif plant == "mismatch":
            response["imports"] = []
        elif plant == "bad_scope":
            facts["scopes"][0]["kind"] = []
        else:
            response["schema_version"] = gate._FOREIGN_IMPORT_PROTOCOL
        return SimpleNamespace(
            stdout=json.dumps(response).encode(), stderr=b"", returncode=0
        )

    monkeypatch.setattr(gate.subprocess, "run", fake_run)
    with pytest.raises(gate.GateAcquisitionError):
        gate._imports_under_interpreter(
            b"import dotmac_files\n",
            executable="python",
            product="test",
            revision="a" * 40,
            interpreter="python",
            module="app",
            path="app.py",
            is_package=False,
            available_modules=frozenset({"app"}),
            callable_facts=True,
        )


def test_missing_product_facts_cannot_establish_nonuse():
    with pytest.raises(gate.GateDerivationError, match="missing production source"):
        gate._check_callable_demand(
            _facts({"pkg": LOADER}), production_modules=frozenset({"app"})
        )


def test_same_named_submodule_does_not_hide_package_callable():
    facts = _facts(
        {
            "app": "from pkg import load\nload()\n",
            "pkg": LOADER,
            "pkg.load": "pass\n",
        }
    )
    with pytest.raises(gate.GateDerivationError, match="dynamic import"):
        gate._check_callable_demand(facts, production_modules=frozenset({"app"}))


@pytest.mark.parametrize(
    "expression",
    [
        "[None for load in ()]",
        "{None for load in ()}",
        "{load: None for load in ()}",
        "(None for load in ())",
    ],
)
def test_comprehension_target_does_not_shadow_later_global_loader(expression):
    with pytest.raises(gate.GateDerivationError, match="dynamic import"):
        _check(
            "from pkg import load\ndef boot():\n    "
            + expression
            + "\n    load('x')\nboot()\n"
        )


@pytest.mark.parametrize(
    "body",
    [
        "class Boot:\n    load = 0\n    def run(self):\n"
        "        load('x')\nBoot().run()\n",
        "class Boot:\n    load = 0\n    def run(self):\n"
        "        def inner():\n            load('x')\n        inner()\nBoot().run()\n",
        "class Outer:\n    load = 0\n    class Boot:\n"
        "        def run(self):\n            load('x')\nOuter.Boot().run()\n",
    ],
)
def test_method_free_names_skip_class_namespace(body):
    with pytest.raises(gate.GateDerivationError, match="dynamic import"):
        _check("from pkg import load\n" + body)


def test_import_only_demands_dynamic_module_getattr():
    with pytest.raises(gate.GateDerivationError, match="dynamic import"):
        _check(
            "from pkg import unused\n",
            exports=(
                "from importlib import import_module\n"
                "def __getattr__(name):\n    return import_module(name)\n"
            ),
        )


@pytest.mark.parametrize(
    "prefix, condition",
    [
        ("TYPE_CHECKING = True\n", "TYPE_CHECKING"),
        ("from typing import TYPE_CHECKING\nTYPE_CHECKING = True\n", "TYPE_CHECKING"),
        ("import typing\ntyping.TYPE_CHECKING = True\n", "typing.TYPE_CHECKING"),
        (
            "from typing import TYPE_CHECKING\nglobals()['TYPE_CHECKING'] = True\n",
            "TYPE_CHECKING",
        ),
    ],
)
def test_rebound_type_checking_guard_cannot_hide_runtime_loader(prefix, condition):
    with pytest.raises(gate.GateDerivationError, match="dynamic import"):
        _check("from pkg import load\n" + prefix + f"if {condition}:\n    load('x')\n")


def test_proven_typing_sentinel_preserves_type_only_guard():
    _check(
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n"
        "    from pkg import load\n    load('x')\n"
    )


def test_comprehension_local_shadow_is_not_an_enclosing_binding():
    _check("from pkg import load\ndef boot():\n    [load() for load in ()]\nboot()\n")


def test_type_checking_guard_keeps_static_edges_when_rebound():
    facts = _facts(
        {
            "app": (
                "from typing import TYPE_CHECKING\nTYPE_CHECKING = True\n"
                "if TYPE_CHECKING:\n    import pkg.loader\n"
            )
        }
    )
    assert "pkg.loader" in facts["app"]["imports"]


@pytest.mark.parametrize("receiver", ["self", "__class__"])
def test_class_callable_attributes_remain_demanded(receiver):
    with pytest.raises(gate.GateDerivationError, match="dynamic import"):
        _check(
            "from pkg import load\nclass Boot:\n    callback = load\n"
            f"    def run(self):\n        {receiver}.callback('x')\nBoot().run()\n"
        )


def test_lambda_local_walrus_does_not_shadow_enclosing_global_loader():
    with pytest.raises(gate.GateDerivationError, match="dynamic import"):
        _check(
            "from pkg import load\ndef boot():\n"
            "    unused = lambda: (load := 0)\n    load('x')\nboot()\n"
        )


def test_lambda_parameters_are_local_but_defaults_execute_outside():
    _check("from pkg import load\nunused = lambda load: load('x')\n")
    with pytest.raises(gate.GateDerivationError, match="dynamic import"):
        _check("from pkg import load\nunused = lambda value=load('x'): value\n")


@pytest.mark.parametrize(
    "declaration",
    [
        "class Base:\n    def __init_subclass__(cls):\n        cls.loader('x')\n"
        "class Derived(Base):\n    loader = load\n",
        "class Meta(type):\n    def __new__(meta, name, bases, ns):\n"
        "        ns['loader']('x')\n"
        "        return super().__new__(meta, name, bases, ns)\n"
        "class Derived(metaclass=Meta):\n    loader = load\n",
    ],
)
def test_class_creation_exposes_namespace_to_base_and_metaclass_hooks(declaration):
    with pytest.raises(gate.GateDerivationError, match="dynamic import"):
        _check("from pkg import load\n" + declaration)


def test_comprehension_walrus_outer_binding_is_explicitly_uncertain():
    with pytest.raises(gate.GateDerivationError, match="cannot discharge"):
        _check("import pkg\n[None for item in () if (callback := item)]\n")


def test_exception_and_pattern_captures_have_local_bindings():
    _check(
        "from pkg import load\ndef boot(value):\n"
        "    try:\n        raise ValueError()\n"
        "    except ValueError as load:\n        str(load)\n"
        "    match value:\n        case {'item': load, **rest}:\n"
        "            str(load)\n"
    )


@pytest.mark.parametrize(
    "suite",
    [
        "    if False:\n        load = 0\n    load('x')\n",
        "    load = 0\n    del load\n    load('x')\n",
        "    try:\n        raise ValueError()\n"
        "    except ValueError as load:\n        pass\n    load('x')\n",
        "    load('x')\n    def load(value):\n        pass\n",
    ],
)
def test_class_suite_dynamic_name_lookup_keeps_module_fallback(suite):
    with pytest.raises(gate.GateDerivationError, match="dynamic import"):
        _check("from pkg import load\nclass Boot:\n" + suite)


def test_nested_class_local_declaration_cannot_hide_module_global_fallback():
    with pytest.raises(gate.GateDerivationError, match="dynamic import"):
        _check(
            "from pkg import load\ndef boot():\n    load = 0\n"
            "    class Boot:\n        load('x')\n        load = 1\nboot()\n"
        )


def test_class_lookup_cannot_use_conditional_literal_as_import_proof():
    with pytest.raises(gate.GateDerivationError, match="dynamic import"):
        _check(
            "from importlib import import_module\nname = unknown\n"
            "class Boot:\n    if False:\n        name = 'pkg'\n"
            "    import_module(name)\n"
        )
