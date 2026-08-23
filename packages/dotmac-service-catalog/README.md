# dotmac-service-catalog

Owns stable technical service specifications and plan families, their
effective-dated published versions, typed characteristic values, and declared
eligibility inputs. One family can group many specifications, so a speed or
access variant does not require another commercial offer. It owns no offer,
price, discount, contract, subscription, billing cycle, tax, or fixed
recurrence.

The tenant-only `sc` lineage owns `mod_svc_cat`; services require an explicit
`TenantScope` and mutate/flush inside the caller's transaction.

An adopter publishes values such as `DOWNLOAD_MBPS`, `UPLOAD_MBPS`,
`ACCESS_TYPE`, and `AGGREGATION` on a `ServiceSpecificationVersion`. Those
codes remain product-declared technical semantics; the package only validates
their declared primitive type and preserves the applied version and source
evidence.
