# Uber Receipt Downloader Guidelines

## Environment & Dependencies
- Python >=3.12
- Uses `uv` package manager (manages dependencies via script header)
- Main dependency: `playwright` for browser automation

## Run Commands
```bash
# Run the script
./uber-receipt-download.py --all
# Download from date range
./uber-receipt-download.py --start-date 2024-01-01 --end-date 2024-03-31
# Last 30 days
./uber-receipt-download.py --days 30
```

## Code Style Guidelines
- **Imports**: Standard library first, then third-party, then local modules
- **Type Hints**: Use full type annotations from `typing` module
- **Error Handling**: Use try/except blocks with specific exception handling
- **DocStrings**: Triple-quote docstrings for classes and methods
- **Class Structure**: Class attributes first, then methods with logical grouping
- **Async Pattern**: Use `async`/`await` for asynchronous operations
- **Variable Naming**: snake_case for variables/functions, PascalCase for classes
- **String Formatting**: f-strings preferred for string interpolation
- **Comments**: Use comments to explain complex logic, not obvious operations

## Price Extraction Logic
- Supports multiple currencies (USD, EUR, GBP, JPY, INR, etc.)
- Uses JavaScript evaluation to extract prices from page content
- Three-tier approach prioritizes accuracy:
  1. Look for "Tag" text followed by currency symbol and number
  2. Look for currency symbol immediately followed by number
  3. Fallback to second number found (first is usually distance)
- Filename format: `{amount}-{YYYY-MM-DD}-{trip_id}.pdf` (no currency code)