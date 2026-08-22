# dotmac-workflow-runtime

This module owns tenant user-authored workflow execution instances, ordered
checkpoints, finite leases, retries and explicit repair evidence. ERP's generic
execution behavior is the product-first source.

Definitions, subjects and outputs are opaque versioned references. Assemblies
execute domain/provider intents and return observations; this package performs
no domain mutation, transport, scheduling or arbitrary code execution.
