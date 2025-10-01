# Automating Uber business receipt downloads

I needed to download a bunch of Uber receipts for my business expenses, and while
Uber provides a way to manually download receipt PDFs one at a time through their
website, there's no batch download option. I asked Claude to help me write a
Python script that would automate this process.

The script uses Playwright to control an existing browser session (to avoid
having to handle login credentials), navigates through Uber's trip history,
and downloads each receipt as a PDF. The trickiest parts were handling the modal
dialogs that appear when requesting receipts, and extracting the correct cost
from the trip details page for the filename.

After some back-and-forth testing with Claude using its access to a test browser,
we got it working reliably. The script now downloads receipts with filenames in
a consistent format (24.06-2025-09-18-tripid.pdf), making them easy to
organize and process for accounting. It supports multiple currencies (USD, EUR,
GBP, etc.) and intelligently extracts the actual trip price rather than other
numeric values like distance.

To use it, you need the Python jack-of-all-trades, `uv`, [installed](https://docs.astral.sh/uv/getting-started/installation/). Run your browser
with remote debugging enabled (e.g., `chromium --remote-debugging-port=9222`), then
run the script with your desired options:

```bash
# Download all available receipts
./uber-receipt-downloader.py --all

# Download receipts from a specific date range
./uber-receipt-downloader.py --start-date 2024-01-01 --end-date 2024-03-31

# Download receipts from the last 30 days
./uber-receipt-downloader.py --days 30
```

