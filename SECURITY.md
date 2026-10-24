# Security and deployment

This change strengthens the application. It does not certify zero risk or deploy an internet-facing site. Live HTTPS, persistent storage, backup scheduling and monitoring must still be configured and verified on the chosen host.

## Local use

Install the pinned dependencies with `python -m pip install -r requirements.txt`, then run `python server.py` or Start ERP.cmd. The server now uses Waitress on loopback, not Python's development HTTP server. A checkout-local `.runtime-deps` installation is supported for this workstation and is excluded from GitHub.

Local mode remains loopback-only. Existing users and business records are preserved. Schema migration adds security fields and invalidates legacy sessions once; sign in again. Supported old password hashes are verified and upgraded on successful login, so an existing password is not silently changed. New passwords require 15–128 characters; existing shorter passwords can still sign in and should be changed in Account security.

## Application protections

- Strict host, origin, JSON content type, body size and CSRF checks; no cross-origin credential access.
- Security headers and Content Security Policy on static/API/error responses. Production cookies use the __Host- prefix, Secure, HttpOnly and SameSite=Strict; HTTPS requests receive HSTS.
- Persistent failed-login limits: five failures per account or fifty per client address in a fifteen-minute window. Expired limits clear automatically. Error messages do not disclose whether an account exists. Sensitive password/MFA operations are also rate-limited.
- Random hashed session tokens, per-session CSRF secrets, rotation on login, thirty-minute idle expiry and eight-hour absolute expiry. Logout affects the current device; password or access changes revoke all target-user sessions.
- PBKDF2-SHA256 with 600,000 iterations for new/updated hashes, individual random salts, and constant-time digest comparisons.
- Role checks on business mutations remain enforced by the server. All signed-in roles currently share read access to ordinary business records; only Admin can read user-management/audit data. Review this existing read-access policy before deployment if stronger departmental isolation is needed.
- Production Admin accounts cannot read or modify business records until an authenticator has been enrolled and verified. There is no automatic MFA bypass on administrator password reset.
- TOTP uses six digits and thirty-second time steps. A verified time step cannot be reused. Setup expires after ten minutes. Authenticator secrets are encrypted with Fernet; recovery codes are random, stored as hashes and consumed once. No external QR-code service receives the secret.
- A password plus a current authenticator or unused recovery code is required to replace an existing authenticator. Existing verification remains active until the replacement is confirmed. Replacing it rotates recovery codes and revokes old sessions.
- Production user creation, password resets and role/access edits additionally require the acting Admin password and a fresh authenticator/recovery code. Retry-safe save records store a keyed HMAC fingerprint instead of request bodies, so user passwords and verification codes are not retained in the save cache. Legacy raw request caches are removed during migration; business records are preserved.
- Error responses contain a request ID rather than internal tracebacks. Passwords, OTPs, session tokens, setup keys and recovery codes are excluded from application audit entries.

## Production configuration (not live yet)

1. Choose a host that supports Python and a persistent volume for SQLite. This application is intended for one application instance; do not deploy independent SQLite copies or multiple replicas. Use provider-encrypted storage with restrictive service-account permissions.
2. Set ERP_ENV=production. Set ERP_DB to an absolute persistent path outside the source directory. Production startup refuses a source-folder database. Restore the business database through a controlled migration if needed; never upload it to the public repository.
3. Set ERP_PUBLIC_ORIGIN to the exact HTTPS origin, e.g. https://erp.example.com, with no path or wildcard. Local mode cannot bind publicly. Production requests with unexpected hosts or HTTP are rejected.
4. Terminate TLS at a managed HTTPS proxy. Set ERP_TRUSTED_PROXY to the exact trusted proxy IP and prevent clients from connecting directly to the application port. Only forwarded scheme/client-address headers from that configured peer are accepted by Waitress. Never use a wildcard proxy. Direct spoofed forwarded headers do not grant HTTPS access.
5. Generate independent secret values privately: `python maintenance.py --generate-secrets data/keys`. The command writes owner-readable key/token files and prints paths only. Put their contents into the hosting secret store: setup.token → ERP_SETUP_TOKEN, mfa.key → ERP_MFA_KEY, backup.key → ERP_BACKUP_KEY. Do not paste values into chat, commits, screenshots or logs. Copy any existing local MFA key when migrating enrolled users; startup checks that the configured key can decrypt existing authenticator secrets. Never overwrite or casually rotate these keys.
6. For an empty production database, ERP_SETUP_TOKEN is required before startup. The first-admin form requires that token. After the first account is created, setup is permanently closed while users exist; remove the bootstrap token from the running environment. Production Admin then completes authenticator verification before accessing business data. There are no default production credentials.
7. Keep server clocks synchronized, store recovery codes privately, and confirm HTTPS login/logout, role restrictions and session expiry on the actual host. Run the full test suite before release. Do not enable a development server or debug mode.

Example variable names are in .env.example; it contains no credentials and is not automatically loaded. Dockerfile uses a non-root user and a persistent data volume. A production container intentionally refuses to start without its required configuration. Publishing the image/port alone is not deployment completion.

## Encrypted backup and restore

Run `python maintenance.py --backup /private/backup-directory` with ERP_BACKUP_KEY configured. SQLite's online backup API captures a consistent snapshot including WAL state. Integrity and foreign keys are checked before the archive is encrypted. Temporary plaintext files are restricted and removed. Final files have the .sqlite3.fernet suffix and are excluded from GitHub and Docker contexts.

Run `python maintenance.py --verify /private/backup-directory/archive.sqlite3.fernet` to decrypt into temporary storage and verify the database. Run `python maintenance.py --restore /private/archive.sqlite3.fernet --destination /private/recovery/new.sqlite3` for a new restore destination. Existing files, including the live database, are never overwritten by this helper. Recovered sessions, pending MFA enrollments and old recovery-code lists are invalidated. Keep the original ERP_MFA_KEY for existing authenticator secrets and provision fresh recovery codes by replacing the authenticator after signing in with the app.

The helper currently limits plaintext database snapshots to 128 MiB to bound encryption memory use. For larger databases, select a streaming/provider-managed backup process. Configure the host to schedule backups, retain a separate off-site copy, alert on failures, and perform periodic restore drills. Those provider tasks have not been activated because no hosting account is configured. A lost encryption key cannot be recovered from the encrypted archive; keep keys separately and securely backed up. Windows file permissions also depend on the user's filesystem ACLs; production storage should use a dedicated service account.

## Release checks

Run:

- python test_erp.py
- python test_http.py
- python test_security.py
- node test_gsm_ui.cjs
- node test_product_ui.cjs
- node test_search_ui.cjs
- python -m pip_audit --strict --disable-pip --no-deps -r requirements.txt

GitHub Actions runs these checks and builds the production image. Dependabot proposes dependency/action updates for review; it does not merge them or deploy automatically. Infrastructure, domain, MFA recovery operations and live restore verification still need owner/operator sign-off. The local application is not exposed merely by merging this change.

## Reporting a security issue

Do not post passwords, tokens, customer records, database backups or exploitable production details in a public GitHub issue. Contact the business owner privately and identify the affected version and a minimal reproducible case using synthetic data.
