# dotmac-forms

`dotmac-forms` owns tenant form definitions, immutable published versions and
validated submission/answer snapshots. It was extracted product-first from
ERP's seven-table Forms engine under ADR-0040.

Products retain subject lifecycle and consequences. Files retains stored bytes;
file answers carry opaque references only. Workflow Runtime may carry a form
version reference through an assembly but neither package imports the other.

## Native validation candidate

The unreleased `0.2.0a1` candidate batches structural answer validation in a private Rust extension. Established Python Unicode/Decimal/date/URL/email parsing and every persistence/lifecycle decision retain their owners. Matching wheels require no compiler; source builds require the exact packaged Rust/build-tool pins and a C linker. Hosted qualification and publication remain separate.
