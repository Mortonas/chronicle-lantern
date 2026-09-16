# Privacy and Security

Chronicle Lantern is designed for local campaign preparation. A user's vault, configuration, campaign state, caches, rewrite sessions, provider credentials, and generated working material are private runtime data; they are not part of the public release.

## Data boundaries

- Markdown is indexed locally and retains stable source identity.
- Provider calls are optional and remain behind application adapters.
- Canonical identities, facts, relationships, selection, and ordering are computed by deterministic code.
- Model output is untrusted presentation data. It must pass feature-specific validation or the application uses a grounded fallback.
- A clean installation starts in setup mode. No vault, provider, search, rewrite, or Club-cache operation begins until the complete configuration is valid.
- Configuration diagnostics identify only the selected source class and error category. They redact paths, endpoints, YAML values, credential references, and exception bodies.
- Credentials are supplied through supported environment references, not stored as literal YAML values.

## Cache isolation

The public build uses a versioned, content-addressed Chronicle Lantern cache namespace. Former installation-local caches are never read or imported. Unrecognized files are left untouched, and an unavailable active cache stops Club generation before any provider call.

## Public-release controls

The public repository is generated from an immutable private commit using an explicit allow-list. Every exported path is inventory-classified, binary documentation assets require hash-bound visual and OCR review, Actions are commit-pinned with read-only repository permissions, and the final candidate is scanned together with its one-commit Git database.

Security reports must follow [../SECURITY.md](../SECURITY.md). Do not include real vault excerpts, configuration values, credentials, or identifying local paths.
