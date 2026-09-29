# Cover Letter PDF Extractor

This project extracts text from all PDF cover letters in the `cover_letters/` folder and saves each result as a `.txt` file in `extracted_letters/`.

## Structure

```text
cover-letter-ai/
├── cover_letters/
│   └── *.pdf
├── extracted_letters/
├── src/
│   └── pdf_extractor.py
├── requirements.txt
└── README.md
```

## Install dependencies

```bash
pip install -r requirements.txt
```

## Run the extractor

From the project root:

```bash
python src/pdf_extractor.py
```

The script will scan every PDF in `cover_letters/`, extract text page by page, normalize whitespace, and save matching `.txt` files into `extracted_letters/`.
