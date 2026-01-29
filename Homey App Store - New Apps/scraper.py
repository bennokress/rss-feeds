#!/usr/bin/env python3
"""
Scraper for Homey App Store - New Apps.
Generates an RSS feed from https://homey.app/en-us/apps/homey-pro/
"""

import csv
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from feedgen.feed import FeedGenerator

LISTING_URL = "https://homey.app/en-us/apps/homey-pro/"
SCRIPT_DIR = Path(__file__).parent
ARTICLES_FILE = SCRIPT_DIR / "articles.tsv"
FEED_FILE = SCRIPT_DIR / "feed.xml"
MAX_APPS = 50
TIMEZONE = ZoneInfo("Europe/Berlin")
FETCH_TIMEOUT = 60
MAX_RETRIES = 3


def fetch_html(url: str) -> str:
    """Fetch a page and return raw HTML."""
    response = requests.get(url, timeout=FETCH_TIMEOUT)
    response.raise_for_status()
    response.encoding = "utf-8"
    return response.text


def parse_html(html: str) -> BeautifulSoup:
    """Parse HTML string into BeautifulSoup."""
    return BeautifulSoup(html, "html.parser")


def to_locale_agnostic_url(url: str) -> str:
    """
    Convert locale-specific URL to locale-agnostic URL.
    https://homey.app/en-us/app/{id}/ -> https://homey.app/a/{id}
    """
    # Extract app ID from URL like /en-us/app/io.home-connect/
    if "/app/" in url:
        parts = url.split("/app/")
        if len(parts) == 2:
            app_id = parts[1].rstrip("/")
            return f"https://homey.app/a/{app_id}"
    return url


def parse_new_apps(html: str, existing_ids: set[str]) -> list[str]:
    """
    Parse "New Apps" section from the listing page.
    Returns list of app URLs for apps not in existing_ids.
    Skips known IDs but continues parsing (doesn't stop).
    """
    soup = parse_html(html)
    new_app_urls = []

    # Find the "New Apps" section - look for heading containing "New apps" (case-insensitive)
    new_apps_section = None
    for heading in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
        if "new apps" in heading.get_text().lower():
            # Find the parent section or the next sibling list
            parent = heading.find_parent("section")
            if parent:
                new_apps_section = parent
            else:
                # Try to find the next ul/ol sibling or parent container
                parent = heading.find_parent()
                if parent:
                    new_apps_section = parent
            break

    if not new_apps_section:
        # Fallback: look for any element with "new apps" in text
        for elem in soup.find_all(text=lambda t: t and "new apps" in t.lower()):
            parent = elem.find_parent()
            if parent:
                # Get the grandparent which likely contains the app list
                grandparent = parent.find_parent()
                if grandparent:
                    new_apps_section = grandparent
                    break

    if not new_apps_section:
        print("Could not find 'New Apps' section")
        return []

    # Find all app links in the section
    for link in new_apps_section.select("a[href*='/app/']"):
        href = link.get("href", "")
        if not href:
            continue

        # Make absolute URL if needed
        if href.startswith("/"):
            href = f"https://homey.app{href}"

        # Extract app ID from URL
        if "/app/" in href:
            app_id = href.split("/app/")[1].rstrip("/")

            # Skip if we already have this app
            if app_id in existing_ids:
                print(f"Skipping known app: {app_id}")
                continue

            # Avoid duplicates in this run
            if href not in new_app_urls:
                new_app_urls.append(href)
                print(f"Found new app: {app_id}")

            if len(new_app_urls) >= MAX_APPS:
                print(f"Reached {MAX_APPS} new apps, stopping parse")
                break

    return new_app_urls


def load_existing_apps() -> list[dict]:
    """
    Load existing apps from TSV file.
    Returns list of app dicts in order (newest first).
    """
    apps = []
    if ARTICLES_FILE.exists():
        with open(ARTICLES_FILE, "r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f, delimiter="\t")
            for row in reader:
                apps.append({
                    "id": row["ID"],
                    "name": row["Name"],
                    "description": row["Description"],
                    "url": row["URL"],
                    "image": row["Image"],
                    "developer": row["Developer"],
                    "timestamp": row["Timestamp"],
                })
    return apps


def save_apps(apps: list[dict]) -> None:
    """Save apps to TSV file."""
    with open(ARTICLES_FILE, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, delimiter="\t")
        writer.writerow(["ID", "Name", "Description", "URL", "Image", "Developer", "Timestamp"])
        for app in apps:
            writer.writerow([
                app["id"],
                app["name"],
                app["description"],
                app["url"],
                app["image"],
                app["developer"],
                app["timestamp"],
            ])


def fetch_app_details(url: str) -> dict | None:
    """
    Fetch app detail page and extract: Name, Description, Image, Developer.
    Returns dict with details or None if extraction fails.
    """
    html = fetch_html(url)
    soup = parse_html(html)

    # Extract app ID from URL
    app_id = ""
    if "/app/" in url:
        app_id = url.split("/app/")[1].rstrip("/")

    # Extract name from h1
    name = ""
    h1 = soup.select_one("h1")
    if h1:
        name = h1.get_text(strip=True)

    # Extract description - look for meta description or main description element
    description = ""
    # Try meta description first
    meta_desc = soup.select_one('meta[name="description"]')
    if meta_desc:
        description = meta_desc.get("content", "")

    # If no meta description, try to find description in page content
    if not description:
        # Look for description paragraph
        desc_elem = soup.select_one('[class*="description"]')
        if desc_elem:
            description = desc_elem.get_text(strip=True)

    # Extract large image - look for app icon/image
    image = ""
    # Look for the app icon image - typically in an img tag with the app icon
    for img in soup.select("img"):
        src = img.get("src", "")
        # Look for large.jpg variant of app icon
        if "large" in src or app_id in src:
            image = src
            break

    # If no large image found, try to find any app-related image
    if not image:
        og_image = soup.select_one('meta[property="og:image"]')
        if og_image:
            image = og_image.get("content", "")

    # Extract developer name - look for author link
    developer = ""
    author_elem = soup.select_one('a[href*="/apps/author/"]')
    if author_elem:
        developer = author_elem.get_text(strip=True)
        # Remove app type suffix (may be concatenated without space)
        for suffix in ("Community", "Official"):
            if developer.endswith(suffix):
                developer = developer[:-len(suffix)].strip()
                break

    if not name:
        return None

    return {
        "id": app_id,
        "name": name,
        "description": description,
        "image": image,
        "developer": developer,
    }


def fetch_app_details_with_retry(url: str) -> dict | None:
    """
    Fetch app details with retry logic.
    Returns dict with details on success, None if all retries fail.
    """
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            details = fetch_app_details(url)
            if details and details.get("name"):
                return details
            print(f"  Attempt {attempt}: No name found")
        except Exception as e:
            print(f"  Attempt {attempt}: {e}")

        if attempt < MAX_RETRIES:
            time.sleep(2)

    return None


def parse_timestamp(timestamp_str: str) -> datetime | None:
    """Parse ISO timestamp to datetime."""
    if not timestamp_str:
        return None
    try:
        dt = datetime.fromisoformat(timestamp_str)
        return dt
    except ValueError:
        return None


def generate_rss_feed(apps: list[dict]) -> None:
    """Generate RSS feed from apps."""
    fg = FeedGenerator()
    fg.title("Homey App Store - New Apps")
    fg.link(href="https://community.homey.app/c/apps/7", rel="alternate")
    fg.description("New apps in the Homey App Store")
    fg.language("en")
    fg.ttl(120)
    fg.image(
        url="https://raw.githubusercontent.com/bennokress/rss-feeds/main/Homey%20App%20Store%20-%20New%20Apps/channel-icon.png",
        title="Homey App Store - New Apps",
        link=LISTING_URL,
    )

    # Only include apps with complete data
    for app in apps:
        if not app["name"]:
            continue

        fe = fg.add_entry()
        locale_agnostic_url = to_locale_agnostic_url(app["url"])
        fe.id(locale_agnostic_url)
        fe.title(app["name"])
        fe.link(href=locale_agnostic_url)

        pub_date = parse_timestamp(app["timestamp"])
        if pub_date:
            fe.pubDate(pub_date)

        if app["description"]:
            fe.description(app["description"])

        if app["image"]:
            fe.enclosure(app["image"], 0, "image/jpeg")

        if app["developer"]:
            # RSS author requires email format: "email (name)" or just "email"
            # Use a placeholder email format for compatibility
            fe.author({"name": app["developer"], "email": "noreply@homey.app"})

    fg.rss_file(str(FEED_FILE), pretty=True)


def main() -> None:
    """Main entry point."""
    # Load existing apps
    existing = load_existing_apps()
    existing_ids = {a["id"] for a in existing}
    print(f"Loaded {len(existing)} existing apps")

    # Fetch and parse listing page
    print("Fetching listing page...")
    html = fetch_html(LISTING_URL)

    print("Parsing new apps...")
    new_app_urls = parse_new_apps(html, existing_ids)
    print(f"Found {len(new_app_urls)} new apps")

    # Fetch details for new apps
    new_apps = []
    if new_app_urls:
        print(f"\nFetching details for {len(new_app_urls)} apps...")
        now = datetime.now(TIMEZONE).isoformat()

        for url in new_app_urls:
            print(f"Fetching: {url}")
            details = fetch_app_details_with_retry(url)
            if details:
                details["url"] = url
                details["timestamp"] = now
                new_apps.append(details)
                print(f"  Success: {details['name']}")
            else:
                print(f"  Failed after {MAX_RETRIES} attempts")

        print(f"\nSuccessfully fetched {len(new_apps)}/{len(new_app_urls)} apps")

    # Merge: new apps at the top, then existing
    merged = new_apps + existing

    # Trim to MAX_APPS
    merged = merged[:MAX_APPS]
    print(f"Total apps after trim: {len(merged)}")

    # Save apps
    save_apps(merged)

    # Generate feed
    with_name = sum(1 for a in merged if a["name"])
    print(f"\nGenerating RSS feed ({with_name} apps)...")
    generate_rss_feed(merged)
    print(f"Feed saved to {FEED_FILE}")

    # Output summary for commit message
    if new_apps:
        print("\n--- COMMIT_SUMMARY ---")
        for app in new_apps:
            print(app["name"])
        print("--- END_SUMMARY ---")
        sys.exit(0)
    else:
        print("\nNo new content to commit")
        sys.exit(1)


if __name__ == "__main__":
    main()
