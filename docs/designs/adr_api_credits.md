# API credit monitoring

Status: accepted for the local implementation (2026-10-29).

The new subscription benefit belongs to an API organization. A project key does
not provide the organization Costs API privileges. Published documentation does
not establish an endpoint for promotional grant balance or future issuance.

Options considered:

- Reuse Codex login credentials and private billing endpoints. This would hide
  authentication assumptions, depend on undocumented responses and mix two
  independent products. Rejected.
- Store independent API organization records, query the published Costs API with
  an Admin API key and record grant totals/cycle dates explicitly. Chosen because
  credentials, observable amounts and their limitations remain explicit.

The Python `ApiCredits` service owns validated CRUD, atomic local persistence,
read-only cost queries and sanitized snapshots. Web handlers and the native
window consume the same public snapshot/commands. There is one record per API
organization; an organization ID cannot be assigned to two records.

`_api_accounts/credentials.json` contains local credentials with mode 0600 in a
0700 directory. It is separate from Codex profiles and excluded from Git. Keys
never appear in snapshots, native subprocess arguments, browser storage or logs.
Native commands carry form JSON over standard input. Existing dashboard token
authentication protects every web management route.

Amounts use decimal arithmetic. The manually entered grant total and period are
not inferred from the subscription tier. Organization costs may differ from
credit redemption: `total - reported costs` is explicitly an estimate, never an
authoritative balance. Unconfigured, failed and stale data remain distinct from
zero. A failed refresh preserves the previous successful result. Period changes
invalidate cost snapshots. Expiration does not invent a renewed grant.

Only bounded GET requests to `https://api.openai.com/v1/organization/costs`
are allowed; redirects are rejected. No generation, payment, purchase, claiming,
key creation or spend-limit mutations occur. Network access uses existing proxy
configuration. Tests mock the provider and use temporary storage.

References:

- https://developers.openai.com/api/docs/guides/chatgpt-api-credits
- https://developers.openai.com/api/reference/resources/admin/subresources/organization/subresources/usage/methods/costs
- https://developers.openai.com/api/docs/guides/admin-apis
