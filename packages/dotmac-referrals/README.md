# dotmac-referrals

`dotmac-referrals` is the tenant-plane owner of referral programmes, issued
codes, attribution, conversion evidence and provider-neutral reward requests.
It was extracted product-first from Sub's native referral path.

Party, Customers, Sales/Leads and Billing keep their own rows and decisions.
Every collaborator is an opaque reference; the module never resolves contact
data, creates a customer/lead, or applies a credit. Services receive a caller
`Session` and explicit `TenantScope`, mutate and flush, and never commit or roll
back.

## Service location

Referral capture V2 (`capture_referral_v2`) may carry an opaque
`service_location_ref`: the place the referred prospect would be served, as the
composing product names it. The module stores the token and nothing it resolves
to: no coordinates, no address text. V1 capture is unchanged.
