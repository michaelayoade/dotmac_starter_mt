# dotmac-referrals

`dotmac-referrals` is the tenant-plane owner of referral programmes, issued
codes, attribution, conversion evidence and provider-neutral reward requests.
It was extracted product-first from Sub's native referral path.

Party, Customers, Sales/Leads and Billing keep their own rows and decisions.
Every collaborator is an opaque reference; the module never resolves contact
data, creates a customer/lead, or applies a credit. Services receive a caller
`Session` and explicit `TenantScope`, mutate and flush, and never commit or roll
back.
