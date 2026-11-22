# Example documents

`invoice.html` is an invented electricity bill (company, numbers and invoice are made up). The
documents service tests itself on two PDFs made from it by `scripts/make-fixtures.sh`:

- `invoice.pdf`: the page printed to PDF, with real text;
- `invoice-scan.pdf`: the same page as a crooked, noisy picture, as from a scanner: text only via OCR.
