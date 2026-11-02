# Platform test fixtures

- `embeddings_ref.json`: fp32 reference vectors for the embedder test.
- `table-page.pdf`: page 5 of https://arxiv.org/abs/2408.09869v5 (PDF: https://arxiv.org/pdf/2408.09869v5), kept with
  pypdfium2. Licence CC BY 4.0; attribution: Auer et al., Docling Technical Report, arXiv:2408.09869.
  It holds "Table 1" plus running text, so the PDF test checks text, layout and a table region, and the OCR test
  rasterises it into an image-only PDF.
