# dotmac-connector-whatsapp

Meta WhatsApp Cloud API connector for the independently deployed Dotmac
Integrator. It verifies subscription handshakes and webhook signatures over the
exact received bytes, emits provider-neutral observations, reads the WABA's
approved message-template catalogue, and delivers product-decided text,
template and media commands through `dotmac-integration` SPI 1.4.

The package owns no persistence, retries, checkpoints, destination selection,
or product decisions. Its manifest declares the exact logical secret bindings
it reads: a required primary signing secret, an optional previous signing
secret for bounded rotation, and a required subscription verify token. It
declares a conditionally required Graph access-token binding and exact egress to
`graph.facebook.com`; ingress verification and normalization themselves remain
network-free. Secret values are resolved and held by the Integrator and never
stored here. The connector does not decide customer-window eligibility,
template choice, conversation state or retry policy.

## What it refuses before the wire call

Meta rejects an unapproved template, a mismatched parameter set and an
unsupported or oversize attachment — but only after the request, and for an
attachment only after the whole body has been streamed. All three are facts the
connector already holds, so it refuses them locally with a typed terminal
outcome and nothing reaches the provider.

**Templates.** `messaging.templates.read.v1` reads the catalogue for a WABA. In
`ConnectorMode.POLL` it emits one `whatsapp.message_template.v1` observation per
(name, language) so the product can own a rebuildable projection of what is
approved. On the send path the same read, filtered to one template name, answers
a single question: may this exact (name, language) go out with these parameters?

The freshness policy is explicit and fails closed:

| state | behaviour |
| --- | --- |
| fresh (age < TTL) | served from memory; no provider call |
| cold (no entry) | read synchronously, then answer |
| stale (age >= TTL) | treated exactly as cold; a stale entry is never served |
| read fails | the entry is evicted and the send is refused |

There is deliberately no stale-while-revalidate. Meta withdraws an approval
without telling the sender, so serving a cached approval past its TTL because
the refresh failed is precisely the assumption this gate exists to remove.
`template_cache_ttl_seconds` (default 300, `0` to disable reuse) and
`template_page_size` are per-binding knobs. The memo lives in process only: it
is rebuildable provider data, never a second authority on approval, and the
connector stores nothing.

**Attachments.** Per-type size limits (`image` 5 MiB, `document` 100 MiB,
`audio` 16 MiB, `video` 16 MiB), the caption length (1024) and the filename
length (255) are the documented defaults of a `media_limits` configuration
object, and a binding may only NARROW them — the schema's maximum is the
provider's own number, so a widened limit is refused at activation rather than
at the provider. A caption is carried by `image`, `document` and `video` only; a
filename by `document` only. Over-length content is refused rather than
truncated: trimming a caption to fit is an edit to a product's message, and that
decision belongs to whoever wrote it.

The supported MIME sets are deliberately not configuration. They are the
provider's contract, and widening one locally would only move the rejection back
to the provider. A size check applies only where the connector holds the bytes;
a `link` or pre-uploaded `media_id` is type-checked but not size-checked,
because its size was never seen.
