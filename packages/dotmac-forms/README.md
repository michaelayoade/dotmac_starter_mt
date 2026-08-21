# dotmac-forms

`dotmac-forms` owns tenant form definitions, immutable published versions and
validated submission/answer snapshots. It was extracted product-first from
ERP's seven-table Forms engine under ADR-0040.

Products retain subject lifecycle and consequences. Files retains stored bytes;
file answers carry opaque references only. Workflow Runtime may carry a form
version reference through an assembly but neither package imports the other.
