# Changelog — dotmac-projects

## 0.1.0a1 — 2026-08-21

Published, installed back from the private index, conformance-checked and
tagged from exact protected-main revision `20d24703` by release run
`32480272392`. Publication is supply-chain evidence only; it composes no product
and moves no authority.

- Adds the tenant-only `mod_projects` lineage for projects, tasks, templates,
  dependency graphs, and task assignees.
- Ports Sub's stale transition, terminal-state, same-project, acyclic graph,
  and dependency-completion guards into a transport-neutral lifecycle.
- Makes dependency type and lag effective in deterministic template scheduling;
  the sources stored both fields but scheduled every edge as zero-lag
  finish-to-start.
- Keeps product subjects and consequences outside the module boundary.
