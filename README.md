# Satata Packaging ERP

A database-backed packaging ERP with local mode and guarded production configuration. It does not use Google Sheets as a backend. See SECURITY.md for authentication, deployment and encrypted backup details.

## Start

Install the pinned dependencies with `python -m pip install -r requirements.txt`. Then double-click **Start ERP.cmd**, and open http://127.0.0.1:8765 in a browser. Keep the server running while using the website. Python 3.14 is available at `C:\Python314\python.exe` on this computer. Alternatively run `python server.py` from this folder.

On the first visit create your own administrator account. No default production password exists. Add other users in Administration → Users & Roles. Sessions expire after thirty idle minutes or eight hours in total. New passwords require 15–128 characters. Account security provides authenticator enrollment and password changes; production Admin access requires verified authenticator setup.

## Included scope

- **Overview**: Dashboard with real database totals and workspace readiness.
- **Proforma Invoices**: Create Proforma, Unit Price Calculation, PI Register, revisions, approval, preview and browser print.
- **Customers**: Customer Directory, Customer Profile and editing.
- **MANUFACTURING**: Product Catalog (add/edit finished product name, HS code and counting unit PCS/DZN/ROLL/KG), Recipes & BOMs, Calculation Rules, Recipe Versions, saved-sample verification.
- **INVENTORY & STOCK**: Stock In, Stock Overview, Material Reservations, Material Movements, Batch Traceability.
- **Trade Finance**: Letters of Credit, Add LC, Maturity Schedule, Collections, partial receipts and outstanding balances.
- **Reports**: Customer-wise Sales (approved proforma values, explicitly not recognized revenue), Stock, Costing, Collection, LC, PI Reports; CSV export.
- **Administration**: Company Settings, Users & Roles, Document Templates, Numbering, Audit Trail.

Sales Orders, Procurement, Production and Dispatch are excluded.

## First-use workflow

1. Review Company Settings and your document terms. Company identity and bank fields were transcribed from the supplied workbook; account numbers and commercial terms are not guessed.
2. Add a customer and receive actual material batches using BoE, lot, receipt date, quantity, unit cost, currency and an explicit USD conversion rate where applicable.
3. Open the MASTER CARTOON 3 PLY recipe and verify an independently checked sample. Other catalog products have **no enabled recipe**, even where the workbook contains formulas; configure and verify them explicitly.
4. Create a Proforma. Choose the saved recipe version, enter dimensions, GSM, PCS quantity, currency, rate and markup. Calculate Unit Price; the price is filled automatically. Add the calculated line; repeat for further lines. Only GSM fields referenced by the selected recipe are required. Two-paper products use Liner GSM and Medium GSM; single-GSM products can use GSM while the other fields remain blank. Unused GSM inputs are ignored, not treated as zero. Missing, zero or invalid required GSM is rejected by the backend. Changes invalidate the current calculation. Currency/rate changes clear added lines so all are recalculated. Multiple lines allocate sequentially across FIFO batches for costing.
5. Save as draft, review the preview, and approve. Approved PI appears in Awaiting LC. A revision retains the PI number, records another snapshot, returns to Draft, and releases prior reservations. Resolve an existing LC draft before revising a PI.
6. Add LC by selecting the PI. Save Draft to keep the PI in Awaiting LC; edit and confirm to set LC Created. The PI remains accessible in LC Created and All Proformas. One LC per PI is enforced, including draft records.
7. Enter actual acceptance and maturity dates; no tenor-based maturity date is assumed. Record partial collections in the LC currency; overpayment is rejected.
8. Reserve materials explicitly if required. Reservation is not physical consumption. Stock is never physically deducted by PI calculation, LC creation or collection. There is no consumption endpoint.

## Confirmed recipe and sample

All dimensions are CM. The user confirmed **per-piece** material quantities and expressly approved the Glue formula found in MANUFACTURING!J2.

- Paper L = L1 + W1 + 6; Paper W = H1 + W1 + 2.
- Liner kg/piece = L × W × Liner GSM × 2 × 2 / 10,000,000 × 1.08.
- Medium kg/piece = L × 1.40 × W × Medium GSM × 1 × 2 / 10,000,000 × 1.08.
- Glue kg/piece = (liner kg before wastage + medium kg before wastage) × 20 / 1000 × 1.05.
- Order quantity is applied exactly once. The original multipliers remain separate and unchanged; their business meanings were not relabeled or inferred.

Reference verification input: L1 60, W1 40, H1 45, Liner GSM 150, Medium GSM 120, Other GSM 150, quantity 1. Paper L 106; W 87. Independently checked results:

| Material | KG / piece |
| --- | ---: |
| Liner | 0.5975856 |
| Medium | 0.334647936 |
| Glue | 0.0181267632 |

Recipe editor uses numeric dimension factors, allowances, material dropdowns, layer factors, separate multipliers, corrugation, wastage and calculation types. No code/formula entry is needed. A saved change creates a new unverified version; it never rewrites an older recipe. The production database starts with an unverified recipe so staff review the sample before issuance.

## Persistence, integrity and roles

SQLite data lives in `data/erp.sqlite3` with WAL files while running. Stop the server before copying the database for a simple backup, or use SQLite's backup API. Do not commit real database files. This is a local single-business application; it is not yet an internet deployment or a managed production service.

Server enforcement includes authenticated cookie sessions, PBKDF2 password hashes, CSRF/origin/host validation, role checks, unique PI and LC numbers, transactional saves, idempotency keys, concurrency serialization, optimistic PI revision checks and audit entries. Prices come from saved server calculations, never client-entered Unit Price values. Decimal arithmetic uses six places for unit prices and half-up cents for PI line totals. Batches preserve their cost currency and conversion rate; PI snapshots preserve recipe, batches, weighted costs and exchange rates. See SECURITY.md for the production safeguards and operating requirements. The implemented costing policy is FIFO allocation with weighted cost over the selected batches. No unapproved aggregate currency conversion occurs.

Roles: Admin (all actions), Sales (PI/customer), Approver (approval/cancellation), Manufacturing (recipes/products), Inventory (stock/material/reservation), Finance (LC/collection), Viewer (read only). All signed-in roles can read shared business records; user management and audit data require Admin. Refine this matrix before any broader deployment if customer-specific data isolation is required.

## Workbook inspection

Read all 12 worksheet XML files in `PERFORMA INVOICE AUTOMATION.xlsx`, including fields, formulas, merged areas, page margins and page orientation. Input fields include beneficiary/applicant, bank, payment duration, dimensions, paper GSM, quantity, markup, HS code and PO number. LC fields include bank/branch, tenor, forwarding/acceptance/maturity dates, references and UP data. Stock uses material, BoE/batch, KG and unit cost.

The print template follows the PRINT PI structure: company header/address, applicant, advising bank/SWIFT, PI/date, shipment details, goods/HS/size/quantity/unit/price/amount, totals/amount in words, payment terms, PO number and signature blocks. It uses browser A4 print CSS and labels document revision/status. It is not a pixel-identical Excel reproduction. The workbook's #REF! signature cell, Google Sheets-dependent formulas and hardcoded/faulty PO/amount-word expressions were not copied. Commercial terms, including overdue interest, remain editable blank settings rather than an inferred financial policy. Costing currently uses PCS because the user confirmed a per-piece basis; DZN/ROLL conversion factors are not invented.

## Verification

Run `python test_erp.py`, `python test_http.py`, and `python test_security.py`. Tests use isolated disposable databases and DEMO records, never the real workspace database. Browser QA also uses an isolated demo server (`qa_server.py`) on port 8766, which must not be used as the real business workspace.

Covered: supplied formula factors and glue, one-time quantity multiplication, independent sample validation, FIFO weighted costing and multiple PI lines, USD conversion, all-material shortages, tamper/stale-input rejection, unique numbering and revision history, approval, draft/confirmed PI-to-LC transition, duplicate/concurrent LC prevention, partial collections and overpayment rejection, stock/reservation atomicity, duplicate receipts, persistence, role enforcement, login/cookies/CSRF, idempotent saves and session invalidation.

Browser checks completed: desktop/mobile layout, automatic price fill, input invalidation, PI save/approval, PI-derived LC fields, draft/confirmed LC handling, Awaiting LC removal, original PI retention and partial collection with recalculated outstanding. Native printer/PDF pagination across long invoices has not been validated. Public hosting, actual TLS/proxy configuration, scheduled off-site backups, monitoring and business-specific print sign-off still require configuration on the chosen host. Encrypted backup/restore and guarded production startup are implemented; see SECURITY.md.

Product counting unit is catalog metadata, not an automatic recipe conversion. Existing per-piece calculations and saved PI snapshots retain their original quantity basis. ROLL/KG conversion awaits the user's specific business rule.

## Search within the current section

Each record table has a labelled search box restricted to that visible section/table. Search terms never fetch or mix records from another menu, tab or PI status filter. Matching is case-insensitive, supports Bangla/English text and requires all typed words to match the same row. Clear restores the active table's rows. Each section remembers its own query during the signed-in session, including refreshes after saves; signing out clears search terms. Reports CSV export follows the visible filtered rows. Search does not change dashboard totals, calculation inputs or saved records. Invoice print tables and editable form line tables are excluded, and printing is never limited by a search filter.

## GitHub source and updates

Source repository: https://github.com/alvinalgalif/SATATA-PAKAGING-ERP (public, as requested by the business owner). `.github/workflows/checks.yml` runs business-rule, HTTP and UI checks on pushes and pull requests. Runtime databases, local previews and credentials are excluded by `.gitignore`. See CONTRIBUTING.md for incremental updates. Live website hosting is not configured. GitHub Pages alone cannot run this Python/SQLite ERP.
