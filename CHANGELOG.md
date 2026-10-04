# Changelog

## 0.1.0

First release.

- `LoamhandClient`: `claim`, `declare_device`, `post_readings` against Loamhand's
  `/api/v1/ingest` API, on a caller-supplied `aiohttp.ClientSession`.
- Batches over the server's 2000-reading limit are split; a later batch refused with 429 is
  retried after the server's `Retry-After`.
- Typed errors for 401, 409 `plan_limit`, 429, 422, a bad claim code, an undeclared device
  and network failure.
- Typed, `mypy --strict` clean.
