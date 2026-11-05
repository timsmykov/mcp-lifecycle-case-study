# Decisions and tradeoffs

The operational integration already had useful tool backends. Replacing them or creating per-agent installations would increase the number of processes, credentials and state copies to maintain. The chosen approach retained backend ownership and added a lightweight transport and lifecycle layer.

## Discovery before execution

A catalog contains `protocols`, an `initialize` result and a `discovery` map, for example `tools/list: {result: {tools: [...]}}`. For command-line use it also needs `watch` source paths and a matching `fingerprint`. Changed files invalidate the catalog; the bridge then uses real discovery. A cache is an optimization, not permission to serve stale tool definitions indefinitely.

## Separate sessions on a shared service

Each bridge instance keeps its own protocol and session ID. A common service does not imply a shared current working directory, selected browser tab or mutable analysis target. Stateful tools need additional explicit ownership and recovery contracts outside this extraction.

## Ambiguous writes

A connection can disappear after the backend committed a write but before the response reached the client. The bridge cannot conclude that the write failed. It emits an error stating that the operation was not retried and requires reconciliation before retrying. A preflight ping may detect an expired session before the next call; session reinitialization does not automatically replay an earlier call.

## Public extraction boundary

The published transport is the substantive integration client, adapted only through its surrounding documentation and tests. The local fixture is a test server, not an alternative MCP platform or production service. Private host dispatch, role registries, company data, provider credentials, real sockets and operational snapshots remain excluded.

## Limitations

This is a bounded integration client, not a complete SDK or proof of protocol conformance. Discovery caching depends on complete watched sources. Server authentication and socket permissions remain deployment responsibilities. No production memory reduction or cost percentage is inferred from whole-host measurements during concurrent activity.
