# Using Dotmac Starter

The official source repository is
https://github.com/michaelayoade/dotmac_starter_mt.

No application, ChatGPT account or individual approval is required to view
the repository or download its source. The old tester-access form was retired
on 2026-11-09. Read the [free-use terms](../LICENSE) before use.

## Get the complete package collection

```sh
git clone https://github.com/michaelayoade/dotmac_starter_mt.git
cd dotmac_starter_mt
```

GitHub's **Code → Download ZIP** also includes the package source, docs,
examples and tests in the selected revision. Use a commit ID to pin a
reproducible checkout. The accompanying inventory's `source_revision` records
the exact baseline reviewed for this preparation.

All package directories are included. There are 102 at the reviewed baseline;
the original RC1 snapshot had 101. See the [complete package inventory](inventories/starter-public-packages.json)
for names, versions, source Git tree identities and provenance declarations.
The [module catalogue](MODULE_CATALOG.md) describes contracts and adoption
status. Presence in the collection does not mean every module is composed into
the reference app, installed in its Docker image, or production-qualified.

## Install, understand and test

Follow the root [README](../README.md) for the reference application setup.
For an individual reusable package, read its README, compatibility notes and
manifest before composing it into an application. Packages may have migration,
entitlement, transport or peer-version requirements; copying all package
directories into one application does not compose those contracts.

The [architecture](ARCHITECTURE.md) describes the implemented boundaries.
The [security documentation](SECURITY.md) describes the current posture.
Production permission is not a production-readiness certification.

## Terms and feedback

Free testing, modification, production use and hosting customer applications
are permitted. Redistribution of covered Starter code is prohibited except
for the GitHub functionality and contributions described in the license.
Third-party rights and notices remain intact. This is source-available,
not an open-source release.

Report problems and reviews through
https://github.com/michaelayoade/dotmac_starter_mt/issues.
Include the commit, package, setup steps, expected behavior and actual result.
Remove secrets and customer data. Follow the security document for sensitive
reports. Feedback is welcome and is not a condition of free use.

## Release review

[Rights and publication record](inventories/starter-public-rights-review.md)
distinguishes owner authorization from independently verified provenance and
records remaining third-party evidence gaps. Earlier three-package evaluation
qualification does not qualify this complete collection. This source access
change does not publish a new container or change existing image digests.
