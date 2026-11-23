# Add a custom field

- **Goal:** add a tenant-specific text field to an existing person without a schema migration.
- **Before you begin:** complete [Your first tenant](../tutorials/first-tenant.md).
- Use the ACME owner's `ACME_TOKEN` and the `PERSON` response from that tutorial
  in the same shell. Sign in again if the token has expired.

## Define the field

```bash
curl -fsS -X POST http://acme.localhost:8000/custom-fields/definitions \
  -H "Authorization: Bearer $ACME_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"entity_type":"party","field_code":"department","field_name":"Department","field_type":"TEXT","is_required":false}'
```

- **Expected:** a field definition for `department`.
- The tutorial owner has the admin role required for this operation.

## Save a value on Alice's record

```bash
PARTY_ID=$(printf '%s' "$PERSON" | jq -er .id)
curl -fsS -X PUT "http://acme.localhost:8000/custom-fields/party/$PARTY_ID/values" \
  -H "Authorization: Bearer $ACME_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"department":"Operations"}'
```

## Read it back

```bash
curl -fsS "http://acme.localhost:8000/custom-fields/party/$PARTY_ID/values" \
  -H "Authorization: Bearer $ACME_TOKEN"
```

- **Expected:** the returned values include `"department":"Operations"`.
- No migration runs between these steps. The value is stored in the party's JSONB
  custom-fields data and validated against its tenant's field definition.

## If something fails

- **401:** sign in again and update the tenant bearer token.
- **403:** check the user's role and the tenant's capability grants.
- **404:** check the tenant hostname, party ID and whether the feature is enabled.
- **Field already exists:** reuse the existing definition or choose a new field code.

## Next step

- See [custom-field architecture](../ARCHITECTURE.md#custom-fields-value-flow)
  before registering an additional entity type.
