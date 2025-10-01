#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "playwright",
# ]
# ///
"""
Uber Receipt Downloader

This script connects to an existing Chrome browser instance and downloads
Uber receipts as PDFs from the Uber riders website.

Launch Chrome with: chrome --remote-debugging-port=9222

[Instructions for remote debugging on Windows and MacOS](https://stackoverflow.com/questions/51563287/how-to-make-chrome-always-launch-with-remote-debugging-port-flag)

"""

import asyncio
import os
import re
import time
import argparse
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any
from playwright.async_api import async_playwright, Page, Browser, BrowserContext

# Configuration
CDP_URL = "http://localhost:9222"  # CDP URL for Chrome browser
DOWNLOAD_DIR = os.path.expanduser("~/Downloads/uber_receipts")  # Directory to save receipts
TRIP_IDS = None  # Set to None to fetch all trips automatically

# JavaScript code to extract trip information from the page
EXTRACT_TRIPS_JS = '''
    () => {
        const tripElements = document.querySelectorAll('div[href^="https://riders.uber.com/trips/"]');
        const trips = [];

        for (const element of tripElements) {
            const href = element.getAttribute('href');
            let tripId = null;
            if (href) {
                const match = href.match(/\\/trips\\/([^\\/?]+)/);
                if (match && match[1]) {
                    tripId = match[1];
                }
            }

            // Try to find the date text
            let dateText = null;
            const dateElement = element.querySelector('div[data-baseweb="block"] div');
            if (dateElement) {
                dateText = dateElement.innerText;
            }

            if (tripId) {
                trips.push({ id: tripId, dateText: dateText });
            }
        }

        return trips;
    }
'''

class DateParser:
    """Utility class for parsing dates from Uber's interface"""

    MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
              "January", "February", "March", "April", "May", "June",
              "July", "August", "September", "October", "November", "December"]

    @classmethod
    def parse_date_text(cls, date_text: str) -> Optional[datetime]:
        """
        Parse date text from Uber's interface.
        Common patterns: "Mar 6 • 2:25 PM", "March 6 • 2:25 PM", "Thursday March 6 2025"
        """
        if not date_text:
            return None

        month_pattern = "|".join(cls.MONTHS)

        # Try pattern with year first
        match = re.search(rf'({month_pattern}) (\d+) (\d{{4}})', date_text)
        if match:
            month, day, year = match.groups()
            return cls._parse_date_components(month, day, year)

        # Try pattern without year
        match = re.search(rf'({month_pattern}) (\d+)', date_text)
        if match:
            month, day = match.groups()
            year = str(datetime.now().year)
            return cls._parse_date_components(month, day, year)

        return None

    @classmethod
    def _parse_date_components(cls, month: str, day: str, year: str) -> Optional[datetime]:
        """Parse date from individual components"""
        date_str = f"{month} {day} {year}"

        for fmt in ["%B %d %Y", "%b %d %Y"]:
            try:
                return datetime.strptime(date_str, fmt)
            except ValueError:
                continue
        return None

    @classmethod
    def format_date(cls, date: datetime) -> str:
        """Format date for filename"""
        return date.strftime("%Y-%m-%d")


class ElementInteractor:
    """Utility class for interacting with page elements"""

    @staticmethod
    async def click_element_with_selectors(page: Page, selectors: List[str],
                                          element_name: str) -> bool:
        """
        Try to click an element using multiple selectors.
        Returns True if successful, False otherwise.
        """
        for selector in selectors:
            try:
                if await page.is_visible(selector, timeout=2000):
                    print(f"Found '{element_name}' with selector: {selector}")
                    await page.click(selector)
                    return True
            except Exception:
                continue

        # Last resort: search by text in all buttons
        try:
            all_buttons = await page.query_selector_all('button')
            for button in all_buttons:
                text = await button.inner_text()
                if element_name.lower() in text.lower():
                    print(f"Found button with text: {text}, clicking...")
                    await button.click()
                    return True
        except Exception:
            pass

        return False

    @staticmethod
    async def close_modal(page: Page):
        """Try to close a modal dialog"""
        close_selectors = [
            'button[aria-label="Close"]',
            '.ReactModalPortal button',
            ':text("×")'
        ]

        for selector in close_selectors:
            try:
                if await page.is_visible(selector, timeout=1000):
                    await page.click(selector)
                    print("Closed modal dialog")
                    return
            except Exception:
                continue

        # Fallback: press Escape
        try:
            await page.keyboard.press("Escape")
            print("Closed modal with Escape key")
        except Exception:
            print("Could not close modal - continuing anyway")


class TripDataExtractor:
    """Handles extraction of trip data from pages"""

    @staticmethod
    async def extract_trips_from_page(page: Page) -> List[Dict[str, Any]]:
        """Extract trip information from the current page"""
        return await page.evaluate(EXTRACT_TRIPS_JS)

    @staticmethod
    async def extract_cost_from_page(page: Page) -> str:
        """Extract the cost value from the trip page"""
        try:
            # Use JavaScript to find the price more accurately
            price = await page.evaluate('''
                () => {
                    // Method 1: Look for text that contains "Tag" followed by a currency symbol and number
                    const allText = document.body.innerText || '';

                    // Look for pattern like "Tag€24.06" or "Tag $24.06"
                    const tagPattern = /Tag[\\s]*[€$£¥₹][\\s]*([\\d]+[.,]\\d{2})/;
                    const tagMatch = allText.match(tagPattern);
                    if (tagMatch && tagMatch[1]) {
                        return tagMatch[1];
                    }

                    // Method 2: Look for currency symbol immediately followed by number
                    // This will prefer prices over distances
                    const currencyPattern = /[€$£¥₹]\\s*([\\d]+[.,]\\d{2})/;
                    const currencyMatch = allText.match(currencyPattern);
                    if (currencyMatch && currencyMatch[1]) {
                        return currencyMatch[1];
                    }

                    // Method 3: If no currency symbol found, look for the second number
                    // (first is usually distance, second is usually price)
                    const allNumbers = allText.match(/\\d+[.,]\\d{2}/g);
                    if (allNumbers && allNumbers.length > 1) {
                        // Return the second number (likely the price)
                        return allNumbers[1];
                    }

                    return null;
                }
            ''')

            if price:
                return price

            print("Warning: Could not extract cost from page")
            return "unknown"
        except Exception as e:
            print(f"Error extracting cost: {e}")
            return "unknown"

    @staticmethod
    async def extract_date_from_page(page: Page) -> str:
        """Extract and format the date from the trip page"""
        try:
            date_element = await page.query_selector('div[data-baseweb="block"] div[data-baseweb="typo-labellarge"]')
            if date_element:
                date_text = await date_element.inner_text()
                parsed_date = DateParser.parse_date_text(date_text)
                if parsed_date:
                    return DateParser.format_date(parsed_date)
        except Exception:
            pass

        # Fallback to current date
        return DateParser.format_date(datetime.now())


class UberReceiptDownloader:
    def __init__(self, cdp_url: str, download_dir: str):
        self.cdp_url = cdp_url
        self.download_dir = download_dir
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.playwright = None

        # Initialize helper classes
        self.date_parser = DateParser()
        self.interactor = ElementInteractor()
        self.extractor = TripDataExtractor()

    async def connect_to_browser(self):
        """Connect to existing Chrome browser instance over CDP"""
        try:
            self.playwright = await async_playwright().start()

            print(f"Connecting to Chrome browser at {self.cdp_url}")
            self.browser = await self.playwright.chromium.connect_over_cdp(self.cdp_url)

            # Get the default browser context
            self.context = self.browser.contexts[0] if self.browser.contexts else await self.browser.new_context(
                ignore_https_errors=True,
                accept_downloads=True,
            )

            print("Successfully connected to Chrome browser")
        except Exception as e:
            print(f"Error connecting to Chrome browser at {self.cdp_url}: {e}")
            print("Make sure Chrome is running with the --remote-debugging-port=9222 flag")
            raise

    async def _load_more_trips(self, page: Page) -> bool:
        """Click 'More' button to load additional trips"""
        more_button_selector = 'button:has-text("More")'

        try:
            if await page.is_visible(more_button_selector, timeout=5000):
                print("Found 'More' button, clicking to load additional trips...")
                await page.click(more_button_selector)
                await asyncio.sleep(2)  # Wait for loading
                return True
        except Exception as e:
            print(f"Note: Could not find or click 'More' button: {e}")

        return False

    async def _filter_trips_by_date(self, trips_with_dates: List[Dict[str, Any]],
                                   start_date: Optional[datetime],
                                   end_date: Optional[datetime]) -> List[str]:
        """Filter trips by date range"""
        trip_ids = []

        if not (start_date or end_date):
            return [trip['id'] for trip in trips_with_dates]

        print(f"Filtering trips between {start_date.strftime('%Y-%m-%d') if start_date else 'earliest'} "
              f"and {end_date.strftime('%Y-%m-%d') if end_date else 'latest'}")

        for trip in trips_with_dates:
            trip_id = trip['id']
            date_text = trip['dateText']

            if not date_text:
                trip_ids.append(trip_id)
                continue

            parsed_date = self.date_parser.parse_date_text(date_text)

            if parsed_date:
                if start_date and parsed_date < start_date:
                    continue
                if end_date and parsed_date > end_date:
                    continue

            trip_ids.append(trip_id)

        # Remove duplicates while preserving order
        seen = set()
        return [x for x in trip_ids if not (x in seen or seen.add(x))]

    async def fetch_trip_ids(self, start_date: Optional[datetime] = None,
                            end_date: Optional[datetime] = None) -> List[str]:
        """Fetch trip IDs from the Uber trips page within a date range"""
        if not self.browser or not self.context:
            raise ValueError("Browser not connected. Call connect_to_browser() first.")

        if not self.page:
            self.page = await self.context.new_page()

        try:
            # Navigate to trips page
            trips_url = "https://riders.uber.com/trips?profile=BUSINESS"
            print(f"Navigating to {trips_url}")
            await self.page.goto(trips_url, wait_until='networkidle', timeout=30000)

            # Wait for the page to load
            print("Waiting for trip elements to load...")
            await self.page.wait_for_selector('div[href^="https://riders.uber.com/trips/"]',
                                             state='visible', timeout=10000)

            # Extract initial trips
            trips_with_dates = await self.extractor.extract_trips_from_page(self.page)
            print(f"Found {len(trips_with_dates)} trip entries on the first page")

            # Load more trips if available
            while await self._load_more_trips(self.page):
                new_trips = await self.extractor.extract_trips_from_page(self.page)

                if len(new_trips) > len(trips_with_dates):
                    print(f"Loaded more trips, now found {len(new_trips)} total")
                    trips_with_dates += new_trips
                else:
                    print("No new trips found after clicking 'More', stopping")
                    break

            # Filter by date
            unique_trip_ids = await self._filter_trips_by_date(trips_with_dates, start_date, end_date)

            print(f"Selected {len(unique_trip_ids)} trips after date filtering")
            return unique_trip_ids

        except Exception as e:
            print(f"Error fetching trip IDs: {e}")
            return []

    async def _navigate_to_receipt(self, trip_id: str) -> bool:
        """Navigate to trip page and open receipt dialog"""
        trip_url = f"https://riders.uber.com/trips/{trip_id}"
        print(f"Navigating to {trip_url}")
        await self.page.goto(trip_url, wait_until='networkidle', timeout=30000)

        # Click "View Receipt" button
        view_receipt_selectors = [
            'button[data-tracking-name="view-receipt-link"]',
            ':text("View Receipt")',
            'button:has-text("View Receipt")',
            '[data-test="view-receipt-button"]'
        ]

        if not await self.interactor.click_element_with_selectors(self.page,
                                                                  view_receipt_selectors,
                                                                  "View Receipt"):
            raise Exception("Could not find or click 'View Receipt' button")

        # Wait for popup
        await asyncio.sleep(1)
        return True

    async def _download_pdf_from_receipt(self, trip_id: str) -> Optional[str]:
        """Download PDF from the receipt dialog"""
        download_pdf_selectors = [
            ':text("Download PDF")',
            'text="Download PDF"',
            'button:has-text("Download PDF")',
            '[data-test="download-pdf-button"]'
        ]

        for selector in download_pdf_selectors:
            try:
                if await self.page.is_visible(selector, timeout=2000):
                    print(f"Found 'Download PDF' button with selector: {selector}")

                    # Extract cost and date before download
                    cost = await self.extractor.extract_cost_from_page(self.page)
                    date_formatted = await self.extractor.extract_date_from_page(self.page)

                    # Set up download handler
                    async with self.page.expect_download() as download_info:
                        await self.page.click(selector)
                        print("Waiting for download to start...")

                        try:
                            download = await asyncio.wait_for(download_info.value, 10.0)

                            # Generate filename and save
                            filename = f"{cost}-{date_formatted}-{trip_id}.pdf"
                            download_path = os.path.join(self.download_dir, filename)

                            os.makedirs(self.download_dir, exist_ok=True)
                            await download.save_as(download_path)
                            print(f"Downloaded receipt to: {download_path}")

                            return download_path

                        except asyncio.TimeoutError:
                            print("Download didn't start within the timeout period")
                            return None
            except Exception:
                continue

        raise Exception("Could not find 'Download PDF' button")

    async def download_receipt(self, trip_id: str) -> Optional[str]:
        """Download a receipt for a specific trip ID"""
        if not self.browser or not self.context:
            raise ValueError("Browser not connected. Call connect_to_browser() first.")

        try:
            if not self.page:
                self.page = await self.context.new_page()

            # Navigate and open receipt
            await self._navigate_to_receipt(trip_id)

            # Download PDF
            download_path = await self._download_pdf_from_receipt(trip_id)

            # Close popup
            await asyncio.sleep(2)
            await self.interactor.close_modal(self.page)

            return download_path

        except Exception as e:
            print(f"Error downloading receipt for trip {trip_id}: {e}")
            return None

    async def download_multiple_receipts(self, trip_ids: Optional[List[str]] = None,
                                        start_date: Optional[datetime] = None,
                                        end_date: Optional[datetime] = None) -> List[tuple[str, Optional[str]]]:
        """Download receipts for multiple trip IDs"""
        if not self.browser:
            await self.connect_to_browser()

        # Fetch trip IDs if not provided
        if not trip_ids:
            print("No trip IDs provided, fetching from Uber...")
            trip_ids = await self.fetch_trip_ids(start_date, end_date)

            if not trip_ids:
                print("No trips found.")
                return []

        print(f"Starting download of {len(trip_ids)} receipts...")

        results = []
        for i, trip_id in enumerate(trip_ids):
            print(f"Downloading receipt {i+1}/{len(trip_ids)} - Trip ID: {trip_id}")
            result = await self.download_receipt(trip_id)
            results.append((trip_id, result))

            # Small delay between downloads
            await asyncio.sleep(2)

        return results

    async def close(self):
        """Close browser connection and clean up resources"""
        try:
            if self.page:
                await self.page.close()
            if self.browser:
                await self.browser.close()
            if self.playwright:
                await self.playwright.stop()
        except Exception as e:
            print(f"Error during cleanup: {e}")


async def main():
    # Parse command line arguments
    parser = argparse.ArgumentParser(description='Download Uber receipts as PDFs')
    parser.add_argument('--trip-id', action='append',
                       help='Specific trip ID(s) to download (can be used multiple times)')
    parser.add_argument('--days', type=int, default=90,
                       help='Number of days back to fetch trips (default: 90)')
    parser.add_argument('--start-date', type=str,
                       help='Start date for trip range (format: YYYY-MM-DD)')
    parser.add_argument('--end-date', type=str,
                       help='End date for trip range (format: YYYY-MM-DD, defaults to today)')
    parser.add_argument('--output-dir', type=str, default=DOWNLOAD_DIR,
                       help=f'Directory to save receipts (default: {DOWNLOAD_DIR})')
    parser.add_argument('--cdp-url', type=str, default=CDP_URL,
                       help=f'Chrome DevTools Protocol URL (default: {CDP_URL})')
    parser.add_argument('--all', action='store_true',
                       help='Download all available trips')

    args = parser.parse_args()

    # Initialize downloader
    downloader = UberReceiptDownloader(args.cdp_url, args.output_dir)

    try:
        await downloader.connect_to_browser()

        # Process date arguments
        start_date = None
        end_date = datetime.now()

        if args.start_date:
            try:
                start_date = datetime.strptime(args.start_date, "%Y-%m-%d")
            except ValueError:
                print(f"Error: Invalid start date format. Using default.")
                start_date = datetime.now() - timedelta(days=args.days)
        else:
            start_date = datetime.now() - timedelta(days=args.days)

        if args.end_date:
            try:
                end_date = datetime.strptime(args.end_date, "%Y-%m-%d")
            except ValueError:
                print(f"Error: Invalid end date format. Using today.")

        # Determine which trip IDs to use
        trip_ids = args.trip_id if args.trip_id else TRIP_IDS

        if trip_ids:
            print(f"Using {len(trip_ids)} provided trip ID(s)")
            results = await downloader.download_multiple_receipts(trip_ids)
        elif args.all or TRIP_IDS is None:
            print(f"Fetching trip IDs from {start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}")
            results = await downloader.download_multiple_receipts(
                trip_ids=None,
                start_date=start_date,
                end_date=end_date
            )
        else:
            print("No trip IDs provided and --all not specified. Please provide trip IDs or use --all.")
            return

        # Print results
        successful = [trip_id for trip_id, path in results if path]
        failed = [trip_id for trip_id, path in results if not path]

        print("\nDownload Results:")
        print(f"Successfully downloaded: {len(successful)}/{len(results)}")

        if failed:
            print(f"Failed downloads: {len(failed)}")
            for trip_id in failed:
                print(f"  - {trip_id}")

        if successful:
            print(f"\nReceipts saved to: {args.output_dir}")

    except Exception as e:
        print(f"Error: {e}")
    finally:
        await downloader.close()


if __name__ == "__main__":
    asyncio.run(main())
