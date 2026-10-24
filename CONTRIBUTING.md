# Updating Satata ERP

The business owner requested a public source repository. Runtime business records and credentials remain excluded.

For each change:

1. Create a branch describing the change.
2. Make the requested update without changing unconfirmed financial or stock rules.
3. Run `python test_erp.py`, `python test_http.py`, `python test_security.py`, `node test_gsm_ui.cjs`, `node test_product_ui.cjs`, and `node test_search_ui.cjs`.
4. Review the changed files and open a pull request. GitHub Actions runs the same checks.
5. Merge after review. Deploy only through the selected hosting service after it has been configured.

Never commit the runtime database, user accounts, sessions, real customer/PI/LC records, exported business data, credentials or private keys. Demo test fixtures contain synthetic credentials and run against disposable databases.

Repository access and website hosting are separate. GitHub Pages cannot run this app's Python API or persist its SQLite database. Before public website deployment, configure a suitable Python application server, HTTPS, trusted host/origin settings, protected first-administrator setup, persistent storage and backups. Local mode binds to loopback. The Waitress production entry point refuses unsafe configuration; see SECURITY.md. No change automatically exposes or deploys the application.

ROLL/KG conversion rules remain pending. Catalog units are stored without inventing financial conversions.
