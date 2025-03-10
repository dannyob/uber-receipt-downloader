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
from playwright.async_api import async_playwright

# Configuration
CDP_URL = "http://localhost:9222"  # CDP URL for Chrome browser
DOWNLOAD_DIR = os.path.expanduser("~/Downloads/uber_receipts")  # Directory to save receipts
# Set to None to fetch all trips automatically
TRIP_IDS = None  

class UberReceiptDownloader:
    def __init__(self, cdp_url: str, download_dir: str):
        self.cdp_url = cdp_url
        self.download_dir = download_dir
        self.browser = None
        self.context = None
        self.page = None
        self.playwright = None

    async def connect_to_browser(self):
        """Connect to existing Chrome browser instance over CDP"""
        try:
            self.playwright = await async_playwright().start()
            
            print(f"Connecting to Chrome browser at {self.cdp_url}")
            self.browser = await self.playwright.chromium.connect_over_cdp(self.cdp_url)
            
            # Get the default browser context
            self.context = self.browser.contexts[0] if self.browser.contexts else await self.browser.new_context(
                ignore_https_errors=True,  # Ignore SSL certificate errors
                accept_downloads=True,     # Enable file downloads
            )
            
            print("Successfully connected to Chrome browser")
        except Exception as e:
            print(f"Error connecting to Chrome browser at {self.cdp_url}: {e}")
            print("Make sure Chrome is running with the --remote-debugging-port=9222 flag")
            raise

    async def extract_cost(self, page):
        """Extract the cost value from the trip page"""
        try:
            # Approach 1: Find all trip detail divs and look for the one with a $ symbol
            cost_divs = await page.query_selector_all('div[data-baseweb="block"][class*="css-iMyxrY"]')
            
            for div in cost_divs:
                text = await div.inner_text()
                if '$' in text:
                    # Use regex to extract just the number part
                    import re
                    price_match = re.search(r'\$(\d+\.\d+)', text)
                    if price_match and price_match.group(1):
                        return price_match.group(1)
            
            # Approach 2: Try to find the Tag icon and get its parent div text
            tag_icon = await page.query_selector('svg[title="Tag"]')
            if tag_icon:
                # Get the parent element that contains the price
                parent_element = await page.query_selector('svg[title="Tag"] + div')
                if parent_element:
                    price_element = await parent_element.query_selector('p')
                    if price_element:
                        price_text = await price_element.inner_text()
                        # Extract the number part
                        import re
                        price_match = re.search(r'\$(\d+\.\d+)', price_text)
                        if price_match and price_match.group(1):
                            return price_match.group(1)
            
            # If we get here, we couldn't find the cost
            print("Warning: Could not extract cost from page")
            return "unknown"
        except Exception as e:
            print(f"Error extracting cost: {e}")
            return "unknown"

    async def fetch_trip_ids(self, start_date: Optional[datetime] = None, end_date: Optional[datetime] = None) -> List[str]:
        """Fetch trip IDs from the Uber trips page within a date range"""
        if not self.browser or not self.context:
            raise ValueError("Browser not connected. Call connect_to_browser() first.")
        
        if not self.page:
            self.page = await self.context.new_page()
        
        try:
            # Navigate to trips page
            trips_url = "https://riders.uber.com/trips"
            print(f"Navigating to {trips_url}")
            await self.page.goto(trips_url, wait_until='networkidle', timeout=30000)
            
            # Wait for the page to load with trip elements
            print("Waiting for trip elements to load...")
            await self.page.wait_for_selector('div[href^="https://riders.uber.com/trips/"]', state='visible', timeout=10000)
            
            # Extract trip IDs and dates using JavaScript evaluation
            trips_with_dates = await self.page.evaluate('''
                () => {
                    // Look for divs with href attributes containing trip IDs
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
            ''')
            
            print(f"Found {len(trips_with_dates)} trip entries on the first page")
            
            # Check if we need to load more trips (click "More" button if it exists)
            try:
                more_button_selector = 'button:has-text("More")'
                
                while await self.page.is_visible(more_button_selector, timeout=5000):
                    print("Found 'More' button, clicking to load additional trips...")
                    await self.page.click(more_button_selector)
                    
                    # Wait for loading to complete
                    await asyncio.sleep(2)
                    
                    # Extract additional trip IDs
                    new_trips_with_dates = await self.page.evaluate('''
                        () => {
                            // Same extraction logic as before
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
                    ''')
                    
                    if len(new_trips_with_dates) > len(trips_with_dates):
                        print(f"Loaded more trips, now found {len(new_trips_with_dates)} total")
                        trips_with_dates = new_trips_with_dates
                    else:
                        print("No new trips found after clicking 'More', stopping")
                        break
            except Exception as e:
                print(f"Note: Could not find or click 'More' button: {e}")
            
            # Filter by date if requested
            trip_ids = []
            
            if start_date or end_date:
                print(f"Filtering trips between {start_date.strftime('%Y-%m-%d') if start_date else 'earliest'} and {end_date.strftime('%Y-%m-%d') if end_date else 'latest'}")
                
                # Process each trip to check date
                for trip in trips_with_dates:
                    trip_id = trip['id']
                    date_text = trip['dateText']
                    
                    # Skip if no date text available
                    if not date_text:
                        trip_ids.append(trip_id)
                        continue
                    
                    # Try to extract date from date_text
                    try:
                        # Common patterns in Uber's interface:
                        # "Mar 6 • 2:25 PM"
                        # "March 6 • 2:25 PM"
                        import re
                        # Look for month names
                        months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", 
                                  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
                                  "January", "February", "March", "April", "May", "June",
                                  "July", "August", "September", "October", "November", "December"]
                        
                        month_pattern = "|".join(months)
                        match = re.search(rf'({month_pattern}) (\d+)', date_text)
                        
                        if match:
                            month, day = match.groups()
                            # Use current year as Uber typically only shows month and day
                            # This is a limitation - for trips more than a year old
                            year = datetime.now().year
                            
                            # Try to parse with both full and abbreviated month names
                            trip_date = None
                            for fmt in ["%B %d %Y", "%b %d %Y"]:
                                try:
                                    trip_date = datetime.strptime(f"{month} {day} {year}", fmt)
                                    break
                                except ValueError:
                                    continue
                            
                            # Check if date is in range
                            if trip_date:
                                if start_date and trip_date < start_date:
                                    continue
                                if end_date and trip_date > end_date:
                                    continue
                        
                        # Include the trip if we couldn't parse the date or it's in range
                        trip_ids.append(trip_id)
                        
                    except Exception as e:
                        # If date parsing fails, include the trip to be safe
                        print(f"Error parsing date for trip {trip_id}: {e}")
                        trip_ids.append(trip_id)
            else:
                # If no date filtering, include all trip IDs
                trip_ids = [trip['id'] for trip in trips_with_dates]
            
            # Remove duplicates while preserving order
            seen = set()
            unique_trip_ids = [x for x in trip_ids if not (x in seen or seen.add(x))]
            
            print(f"Selected {len(unique_trip_ids)} trips after date filtering")
            return unique_trip_ids
            
        except Exception as e:
            print(f"Error fetching trip IDs: {e}")
            return []

    async def download_receipt(self, trip_id: str) -> Optional[str]:
        """Download a receipt for a specific trip ID"""
        if not self.browser or not self.context:
            raise ValueError("Browser not connected. Call connect_to_browser() first.")
        
        try:
            if not self.page:
                self.page = await self.context.new_page()
            
            # Navigate to the trip page
            trip_url = f"https://riders.uber.com/trips/{trip_id}"
            print(f"Navigating to {trip_url}")
            await self.page.goto(trip_url, wait_until='networkidle', timeout=30000)
            
            # Click "View Receipt" button - use known selector from our testing
            view_receipt_selectors = [
                'button[data-tracking-name="view-receipt-link"]',  # This worked in our testing
                ':text("View Receipt")',
                'button:has-text("View Receipt")',
                '[data-test="view-receipt-button"]'
            ]
            
            receipt_clicked = False
            for selector in view_receipt_selectors:
                try:
                    # Check if the selector exists and is visible
                    visible = await self.page.is_visible(selector, timeout=2000)
                    if visible:
                        print(f"Found 'View Receipt' button with selector: {selector}, clicking...")
                        await self.page.click(selector)
                        receipt_clicked = True
                        break
                except Exception:
                    continue
                    
            if not receipt_clicked:
                print("Could not find 'View Receipt' button with any of the tried selectors")
                # One last attempt - try to find it by looking at all buttons
                try:
                    all_buttons = await self.page.query_selector_all('button')
                    for button in all_buttons:
                        text = await button.inner_text()
                        if "receipt" in text.lower() or "view" in text.lower():
                            print(f"Found button with text: {text}, clicking...")
                            await button.click()
                            receipt_clicked = True
                            break
                except Exception as e:
                    print(f"Error in last attempt to find view receipt button: {e}")
                    
            if not receipt_clicked:
                raise Exception("Could not find or click 'View Receipt' button")
            
            # Wait for the popup dialog to appear
            print("Waiting for receipt popup dialog...")
            await asyncio.sleep(1)  # Brief pause to ensure modal is fully loaded
            
            # Click "Download PDF" button within the popup
            download_pdf_selectors = [
                ':text("Download PDF")',  # This worked in our testing
                'text="Download PDF"',
                'button:has-text("Download PDF")',
                '[data-test="download-pdf-button"]'
            ]
            
            pdf_button_found = False
            for selector in download_pdf_selectors:
                try:
                    # Check if the selector exists and is visible
                    visible = await self.page.is_visible(selector, timeout=2000)
                    if visible:
                        print(f"Found 'Download PDF' button with selector: {selector}")
                        pdf_button_found = True
                        
                        # Set up download event handler
                        download_path = None
                        async with self.page.expect_download() as download_info:
                            # Now click the button inside the expect_download context manager
                            await self.page.click(selector)
                            print("Waiting for download to start...")
                            
                            # Wait for the download to start with a timeout
                            try:
                                download = await asyncio.wait_for(download_info.value, 10.0)
                                
                                # Extract the cost from the page before clicking Download
                                cost = await self.extract_cost(self.page)
                                
                                # Get date from the page if possible
                                try:
                                    date_element = await self.page.query_selector('div[data-baseweb="block"] div[data-baseweb="typo-labellarge"]')
                                    date_text = await date_element.inner_text() if date_element else None
                                    
                                    if date_text:
                                        # Extract date from various formats that might appear
                                        import re
                                        
                                        # Try to find patterns like:
                                        # "2:28 PM, Thursday March 6 2025"
                                        # "Mar 6 • 2:25 PM"
                                        
                                        # Look for month names
                                        months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", 
                                                  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
                                                  "January", "February", "March", "April", "May", "June",
                                                  "July", "August", "September", "October", "November", "December"]
                                        
                                        month_pattern = "|".join(months)
                                        date_patterns = [
                                            # Pattern like "March 6 2025"
                                            rf'({month_pattern}) (\d+) (\d{{4}})',
                                            # Pattern like "Mar 6 • 2:25 PM" (year will be current year)
                                            rf'({month_pattern}) (\d+)',
                                        ]
                                        
                                        date_formatted = None
                                        for pattern in date_patterns:
                                            date_match = re.search(pattern, date_text)
                                            if date_match:
                                                try:
                                                    if len(date_match.groups()) == 3:  # Full date with year
                                                        month, day, year = date_match.groups()
                                                        date_str = f"{month} {day} {year}"
                                                        # Try to parse with both full and abbreviated month names
                                                        for fmt in ["%B %d %Y", "%b %d %Y"]:
                                                            try:
                                                                date_obj = datetime.strptime(date_str, fmt)
                                                                date_formatted = date_obj.strftime("%Y-%m-%d")
                                                                break
                                                            except ValueError:
                                                                continue
                                                    else:  # Date without year
                                                        month, day = date_match.groups()
                                                        # Use current year
                                                        year = datetime.now().year
                                                        date_str = f"{month} {day} {year}"
                                                        # Try to parse with both full and abbreviated month names
                                                        for fmt in ["%B %d %Y", "%b %d %Y"]:
                                                            try:
                                                                date_obj = datetime.strptime(date_str, fmt)
                                                                date_formatted = date_obj.strftime("%Y-%m-%d")
                                                                break
                                                            except ValueError:
                                                                continue
                                                except Exception as e:
                                                    print(f"Error parsing date: {e}")
                                                
                                                if date_formatted:
                                                    break
                                        
                                        if not date_formatted:
                                            # If no date could be parsed, use current date
                                            date_formatted = datetime.now().strftime("%Y-%m-%d")
                                    else:
                                        date_formatted = datetime.now().strftime("%Y-%m-%d")
                                except Exception:
                                    # If date extraction fails, use current date
                                    date_formatted = datetime.now().strftime("%Y-%m-%d")
                                
                                # Generate a filename with date, cost and trip ID
                                filename = f"{date_formatted}-{cost}USD-{trip_id}.pdf"
                                
                                download_path = os.path.join(self.download_dir, filename)
                                
                                # Create directory if it doesn't exist
                                os.makedirs(self.download_dir, exist_ok=True)
                                
                                # Save the file
                                await download.save_as(download_path)
                                print(f"Downloaded receipt to: {download_path}")
                                
                            except asyncio.TimeoutError:
                                print("Download didn't start within the timeout period")
                        
                        # Wait for a moment to ensure download completes or dialog closes
                        await asyncio.sleep(2)
                        
                        # Close the popup by clicking outside or on the X button
                        try:
                            # Try specific close button first
                            close_button_selectors = [
                                'button[aria-label="Close"]',
                                '.ReactModalPortal button',
                                ':text("×")'
                            ]
                            
                            for close_selector in close_button_selectors:
                                if await self.page.is_visible(close_selector, timeout=1000):
                                    await self.page.click(close_selector)
                                    print("Closed receipt popup dialog")
                                    break
                            else:
                                # If no close button found, try pressing Escape
                                await self.page.keyboard.press("Escape")
                                print("Closed receipt popup dialog with Escape key")
                        except Exception as e:
                            print(f"Error closing popup: {e} - continuing anyway")
                        
                        return download_path
                except Exception:
                    continue
                    
            if not pdf_button_found:
                # Try to find any download-related element with text
                try:
                    # Extract all text from the page
                    all_text = await self.page.evaluate('''
                        () => {
                            return document.body.innerText;
                        }
                    ''')
                    
                    if "download pdf" in all_text.lower():
                        # Try a more generic selector
                        print("Found 'Download PDF' text, trying generic selector")
                        selector = ':text("Download PDF")'
                        pdf_button_found = True
                    elif "download" in all_text.lower() and "pdf" in all_text.lower():
                        # Try clicking anywhere with download or PDF text
                        print("Found 'Download' and 'PDF' text separately, trying broader selector")
                        selector = ':text-matches("(?i)download|pdf")'
                        pdf_button_found = True
                except Exception as e:
                    print(f"Error searching for download text: {e}")
                    
            if not pdf_button_found:
                raise Exception("Could not find 'Download PDF' button")
            
        except Exception as e:
            print(f"Error downloading receipt for trip {trip_id}: {e}")
            return None

    async def download_multiple_receipts(self, trip_ids: Optional[List[str]] = None, start_date: Optional[datetime] = None, end_date: Optional[datetime] = None):
        """Download receipts for multiple trip IDs"""
        # Connect to browser if not already connected
        if not self.browser:
            await self.connect_to_browser()
        
        # If no trip IDs provided, fetch them
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
            # Small delay between downloads to avoid rate limiting
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
    parser.add_argument('--trip-id', action='append', help='Specific trip ID(s) to download (can be used multiple times)')
    parser.add_argument('--days', type=int, default=90, help='Number of days back to fetch trips (default: 90)')
    parser.add_argument('--start-date', type=str, help='Start date for trip range (format: YYYY-MM-DD)')
    parser.add_argument('--end-date', type=str, help='End date for trip range (format: YYYY-MM-DD, defaults to today)')
    parser.add_argument('--output-dir', type=str, default=DOWNLOAD_DIR, help=f'Directory to save receipts (default: {DOWNLOAD_DIR})')
    parser.add_argument('--cdp-url', type=str, default=CDP_URL, help=f'Chrome DevTools Protocol URL (default: {CDP_URL})')
    parser.add_argument('--all', action='store_true', help='Download all available trips')
    parser.add_argument('--test', action='store_true', help='Test cost extraction on a specific trip ID')
    parser.add_argument('--test-trip-id', type=str, default="1003c9ae-bd1c-48eb-b751-e260c336f7fa", help='Trip ID to use for testing (default: a specific trip ID)')
    
    args = parser.parse_args()
    
    # Use arguments or defaults
    cdp_url = args.cdp_url
    download_dir = args.output_dir
    
    # Initialize downloader
    downloader = UberReceiptDownloader(cdp_url, download_dir)
    
    try:
        await downloader.connect_to_browser()
        
        # Special test mode
        if args.test:
            test_trip_id = args.test_trip_id
            print(f"Testing cost extraction on trip ID: {test_trip_id}")
            
            # Navigate to the trip page
            if not downloader.page:
                downloader.page = await downloader.context.new_page()
                
            trip_url = f"https://riders.uber.com/trips/{test_trip_id}"
            print(f"Navigating to {trip_url}")
            await downloader.page.goto(trip_url, wait_until='networkidle', timeout=30000)
            
            # Extract the cost
            cost = await downloader.extract_cost(downloader.page)
            print(f"Extracted cost: {cost}")
            
            return
        
        # Process date arguments for normal operation
        start_date = None
        end_date = datetime.now()
        
        if args.start_date:
            try:
                start_date = datetime.strptime(args.start_date, "%Y-%m-%d")
            except ValueError:
                print(f"Error: Invalid start date format. Please use YYYY-MM-DD. Using default.")
                start_date = datetime.now() - timedelta(days=args.days)
        else:
            start_date = datetime.now() - timedelta(days=args.days)
        
        if args.end_date:
            try:
                end_date = datetime.strptime(args.end_date, "%Y-%m-%d")
            except ValueError:
                print(f"Error: Invalid end date format. Please use YYYY-MM-DD. Using today.")
        
        # Determine which trip IDs to use
        trip_ids = args.trip_id if args.trip_id else TRIP_IDS
        
        if trip_ids:
            print(f"Using {len(trip_ids)} provided trip ID(s)")
            results = await downloader.download_multiple_receipts(trip_ids)
        elif args.all or TRIP_IDS is None:
            # Fetch trip IDs automatically
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
            print(f"\nReceipts saved to: {download_dir}")
            
    except Exception as e:
        print(f"Error: {e}")
    finally:
        await downloader.close()

if __name__ == "__main__":
    asyncio.run(main())
