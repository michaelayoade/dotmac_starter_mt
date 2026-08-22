# dotmac-service-access-policy

Owns per-service access-policy inputs and the desired allow/restrict/deny
decision. FUP, prepaid, collections and administrators supply observations;
Network Access enforces the decision and remains authoritative for device
state. The tenant-only `sap` lineage owns `mod_serviceaccess`.
