# dotmac-service-catalog

Owns technical service specifications, plan families, characteristic
definitions, and declared eligibility inputs. It owns no offer, price,
discount, contract, subscription, billing cycle, or fixed recurrence.

The tenant-only `sc` lineage owns `mod_svc_cat`; services require an explicit
`TenantScope` and mutate/flush inside the caller's transaction.
