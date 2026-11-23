# RHOS prototype architecture

The first increment uses Python 3.11, FastAPI, and standard-library JSON persistence to get an executable vertical slice into the repository without depending on an unavailable Rust compiler. The prototype is not a distro and does not replace Linux networking.

`rhos.config.Store` owns the canonical desired configuration. It validates input, serializes mutations using an in-process mutex and an advisory file lock, checks expected revisions, and persists candidate edits and commits with write, fsync, and atomic rename. CLI and HTTP handlers call this service. Candidate edits are not running configuration. History retains the last 50 committed revisions.

`rhos.linux` observes the host using structured `ip -j`, and applies a small IPv4 subset through argv-based subprocess calls. nftables rules are generated from validated tokens and confined to RHOS-owned tables. Network application requires `RHOS_APPLY_NETWORK=1`. The adapter validates the nftables batch before touching links and attempts to restore prior addresses, routes, and forwarding state after an apply failure. Configuration commits reapply the prior desired network state if applying the new state or persisting the commit fails. Linux operations span multiple kernel interfaces, so this is compensating rollback for reported synchronous failures, not a kernel-wide atomic transaction or crash-safe confirmed commit. Do not use it on a remote host or production router.

`rhos.auth.AuthStore` bootstraps one local administrator, stores a scrypt password hash and 256-bit session signing key with mode 0600, and issues short-lived signed sessions. The HTTP API requires that session and a CSRF header for mutations. The first administrator is created through an interactive local command. The browser session uses a Secure, HttpOnly, SameSite=Strict cookie. Serve browser sessions over HTTPS. TLS termination and audit logging remain deployment responsibilities.

`rhos.api` serves the versioned HTTP API and a minimal server-rendered page. Unauthenticated requests do not expose the configuration. It defaults to loopback. There is one administrator role at this stage, without user administration or granular RBAC.

The next architectural increments are secure access and confirmed commit/rollback, transactional network apply with namespace-backed integration tests, packaging the control plane in a Debian image, then FRR and richer network features.

The Debian host service is deliberately unprivileged and systemd-hardened. The API can validate and persist desired configuration, but a separate privileged network worker is still required before the packaged service can apply it to the kernel safely. The installer is not an appliance-image builder.
