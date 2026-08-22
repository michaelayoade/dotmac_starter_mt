# dotmac-connector-linkedin

First-party ingress-only LinkedIn plugin for Dotmac Integrator. It verifies the
exact request bytes using the prefix only in the signed material (the header is
the lowercase digest), answers subscription challenges, and normalizes official
batched organization-social plus single Lead Sync notifications into provider-neutral
observations. It performs no provider egress and makes no contact, campaign,
qualification, assignment, ticket, or publication decision.
