# Approved ERP scope

Exact navigation: Overview; Proforma Invoices; Customers; MANUFACTURING; INVENTORY & STOCK; Trade Finance; Reports; Administration.

Excluded: Sales Orders, Procurement, Production, Dispatch.

User-confirmed decisions:
- MASTER CARTOON 3 PLY formula outputs are per-piece material quantities. Apply order quantity once.
- Preserve liner x2x2 and medium x1x2 factors as supplied. Their semantics remain unlabeled rather than inferred.
- Glue formula from Excel MANUFACTURING!J2 is explicitly approved: sum of paper weights before wastage x20/1000 x1.05, then order quantity once.
- No formulas for other products are enabled without explicit configuration and sample validation.
- PI calculation never deducts physical stock. No consumption trigger or endpoint exists.

Workbook inspected at the supplied PERFORMA INVOICE AUTOMATION.xlsx; not used as Google Sheets backend. See README for field/print mapping and verification limits.

Implementation is a local Python/SQLite application with responsive browser interface. Production business database contains no demo financial or stock transactions. Tests and browser QA use separate disposable Demo databases. Public hosting is not configured.
