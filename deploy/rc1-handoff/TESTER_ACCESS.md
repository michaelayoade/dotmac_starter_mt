# Dotmac Starter access

Public form: https://dotmac-starter-access.dotmac-fiber.chatgpt.site/

Approval console: https://dotmac-starter-access.dotmac-fiber.chatgpt.site/admin

## Applicant flow

1. Sign in through ChatGPT.
2. Submit a name, optional GitHub username and terms acceptance.
3. Return to the form to see approval and download availability.

The form records the authenticated identity and email, exact terms text,
terms version and SHA-256, request ID and server acceptance timestamp.
These are private R2 JSON records, separate from the public page.

## Approval

Only the authenticated email olatejuakinlabi@gmail.com may approve, deny
or revoke a request. Each decision records the authenticated approver,
decision time, request ID and terms hash. Repeated submissions with the
same terms preserve the existing request. New terms require acceptance
and approval again. Cross-origin POST requests are rejected.

The download endpoint checks sign-in, current terms acceptance, a matching
approval and a cleared release. It serves the private object through the
Worker rather than exposing a public bucket link.

## Release status

Downloads are blocked by the deployed CLEARED_RELEASE = null configuration.
No artifact has been uploaded. The remaining code-clearance decisions and
supplemental authorization must be resolved before a reviewed deployment
enables a specific artifact key and checksum. Anyone can request access
while this work is pending.

Terms permit free testing, modification, production use and hosted customer
applications, with Starter redistribution prohibited and third-party rights
preserved. The authorization record remains the release-scope authority.

Sites project: appgprj_6af1a77bb2ac8191afed6efd89dc8c15.
Deployed source commit: 36a7fe6e445f7ff415b43038f3075b0fffa7501e.
The public landing page was observed rendering. The authenticated acceptance
and approval flow still requires end-to-end verification with intended
accounts. Hosted anonymous and forged-header checks have been added to CI.
The deployed Worker snapshot is retained in `.github/access-form/` with
synthetic regression checks for required acceptance, immutable acceptance
evidence, approver restriction, stale decisions, approval, revocation and
the closed release gate. These do not impersonate real users or record
synthetic acceptance in live storage.

Hosted verification passed in run 42620047170 at commit
`cf738ceb0506841558f078c2e3e66e9f2829df6e`, including synthetic state
transitions and live anonymous/forged-header rejection. Archive build,
fresh migrations and first API login passed in the same run.

The exact reduced source archive and proposed grant are in
`docs/inventories/starter-reduced-rc1-supplement-for-signature.md`.
Reviewing or approving an access request does not sign that authorization.

Ask applicants to contact the approver to end access or request deletion.
Revoking future downloads does not erase copies already downloaded.
