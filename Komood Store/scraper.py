#!/usr/bin/env python3
"""
Scraper for Komood Store T-shirts.
Generates an RSS feed from https://www.komood.store/collections/t-shirt-kollektion

Uses Shopify's products.json API for reliable data extraction.
"""

import csv
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from feedgen.feed import FeedGenerator

BASE_URL = "https://www.komood.store"
PRODUCTS_API = f"{BASE_URL}/collections/t-shirt-kollektion/products.json"
FEED_LINK = "https://www.komood.store/collections/t-shirt-kollektion"
SCRIPT_DIR = Path(__file__).parent
ARTICLES_FILE = SCRIPT_DIR / "articles.tsv"
FEED_FILE = SCRIPT_DIR / "feed.xml"
TIMEZONE = ZoneInfo("Europe/Berlin")
FETCH_TIMEOUT = 60
MAX_RETRIES = 3


def fetch_json(url: str) -> dict:
    """Fetch a URL and return JSON response."""
    response = requests.get(url, timeout=FETCH_TIMEOUT)
    response.raise_for_status()
    return response.json()


def clean_product_id(handle: str) -> str:
    """
    Clean up product ID for consistent deduplication.
    Removes "ausverkauft-" prefix and "-t-shirt" suffix.
    """
    cleaned = handle
    if cleaned.startswith("ausverkauft-"):
        cleaned = cleaned[len("ausverkauft-"):]
    if cleaned.endswith("-t-shirt"):
        cleaned = cleaned[:-len("-t-shirt")]
    return cleaned


def fetch_all_products() -> list[dict]:
    """
    Fetch all products from Shopify's products.json API.
    Handles pagination automatically.
    Returns list of product dicts with id, title, description, url, image, price.
    """
    all_products = []
    page = 1

    while True:
        url = f"{PRODUCTS_API}?page={page}&limit=250"
        print(f"Fetching products page {page}...")

        try:
            data = fetch_json(url)
        except Exception as e:
            print(f"Error fetching page {page}: {e}")
            break

        products = data.get("products", [])
        if not products:
            print(f"Page {page} is empty, stopping pagination")
            break

        for product in products:
            handle = product.get("handle", "")
            title = product.get("title", "")

            # Clean up ID for deduplication (URL keeps original handle)
            product_id = clean_product_id(handle)

            # Clean up title
            if title.startswith("AUSVERKAUFT: "):
                title = title[len("AUSVERKAUFT: "):]
            if title.endswith(" - T-Shirt"):
                title = title[:-len(" - T-Shirt")]
            elif title.endswith(" - T-shirt"):
                title = title[:-len(" - T-shirt")]

            # Get price from first variant (in cents)
            price_cents = 0
            variants = product.get("variants", [])
            if variants:
                price_cents = variants[0].get("price", 0)
                # Handle both string and int prices
                if isinstance(price_cents, str):
                    try:
                        price_cents = int(float(price_cents) * 100)
                    except ValueError:
                        price_cents = 0

            # Format price as EUR
            if price_cents:
                euros = price_cents // 100
                cents = price_cents % 100
                price = f"€{euros},{cents:02d}"
            else:
                price = ""

            # Get description (HTML stripped by Shopify, but may contain tags)
            body_html = product.get("body_html", "") or ""
            # Simple HTML tag stripping
            import re
            description_text = re.sub(r'<[^>]+>', '', body_html).strip()
            # Clean up whitespace
            description_text = " ".join(description_text.split())

            # Format description with price
            if price and description_text:
                description = f"{price} • {description_text}"
            elif price:
                description = price
            elif description_text:
                description = description_text
            else:
                description = ""

            # Get image URL
            image = ""
            images = product.get("images", [])
            if images:
                image = images[0].get("src", "")

            # Build product URL (uses original handle)
            url = f"{BASE_URL}/products/{handle}"

            if product_id and title:
                all_products.append({
                    "id": product_id,  # Cleaned ID for deduplication
                    "title": title,
                    "description": description,
                    "url": url,  # Original URL
                    "image": image,
                })
                print(f"  Found: {title} (id: {product_id})")

        page += 1

        # Safety limit
        if page > 50:
            print("Reached page limit, stopping")
            break

    return all_products


def load_existing_products() -> list[dict]:
    """
    Load existing products from TSV file.
    Returns list of product dicts in order (newest first).
    """
    products = []
    if ARTICLES_FILE.exists():
        with open(ARTICLES_FILE, "r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f, delimiter="\t")
            for row in reader:
                products.append({
                    "id": row["ID"],
                    "title": row["Title"],
                    "description": row["Description"],
                    "url": row["URL"],
                    "image": row["Image"],
                    "timestamp": row["Timestamp"],
                })
    return products


def save_products(products: list[dict]) -> None:
    """Save products to TSV file."""
    with open(ARTICLES_FILE, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, delimiter="\t")
        writer.writerow(["ID", "Title", "Description", "URL", "Image", "Timestamp"])
        for product in products:
            writer.writerow([
                product["id"],
                product["title"],
                product["description"],
                product["url"],
                product["image"],
                product["timestamp"],
            ])


def parse_timestamp(timestamp_str: str) -> datetime | None:
    """Parse ISO timestamp to datetime."""
    if not timestamp_str:
        return None
    try:
        dt = datetime.fromisoformat(timestamp_str)
        return dt
    except ValueError:
        return None


def generate_rss_feed(products: list[dict]) -> None:
    """Generate RSS feed from products."""
    fg = FeedGenerator()
    fg.title("Komood Shirts")
    fg.link(href=FEED_LINK, rel="alternate")
    fg.description("Neue T-Shirts von Komood Store")
    fg.language("de")
    fg.ttl(120)
    fg.image(
        url="https://raw.githubusercontent.com/bennokress/rss-feeds/main/Komood%20Store/channel-icon.png",
        title="Komood Shirts",
        link=FEED_LINK,
    )

    # Only include products with descriptions
    for product in products:
        if not product["description"]:
            continue

        fe = fg.add_entry()
        fe.id(product["url"])
        fe.title(product["title"])
        fe.link(href=product["url"])

        pub_date = parse_timestamp(product["timestamp"])
        if pub_date:
            fe.pubDate(pub_date)

        fe.description(product["description"])
        if product["image"]:
            fe.enclosure(product["image"], 0, "image/jpeg")

    fg.rss_file(str(FEED_FILE), pretty=True)


def main() -> None:
    """Main entry point."""
    # Load existing products
    existing = load_existing_products()
    existing_ids = {p["id"] for p in existing}
    print(f"Loaded {len(existing)} existing products")

    # Fetch all products from API
    print("\nFetching products from Shopify API...")
    all_products = fetch_all_products()
    print(f"\nFound {len(all_products)} total products")

    # Find new products (not in existing)
    now = datetime.now(TIMEZONE).isoformat()
    new_products = []
    for product in all_products:
        if product["id"] not in existing_ids:
            product["timestamp"] = now
            new_products.append(product)

    print(f"Found {len(new_products)} new products")

    # Merge: new products at the top, then existing (no limit)
    merged = new_products + existing
    print(f"Total products after merge: {len(merged)}")

    # Save products
    save_products(merged)

    # Generate feed
    with_desc = sum(1 for p in merged if p["description"])
    print(f"\nGenerating RSS feed ({with_desc} products with descriptions)...")
    generate_rss_feed(merged)
    print(f"Feed saved to {FEED_FILE}")

    # Output summary for commit message
    if new_products:
        print("\n--- COMMIT_SUMMARY ---")
        for product in new_products:
            print(product["title"])
        print("--- END_SUMMARY ---")
        sys.exit(0)
    else:
        print("\nNo new content to commit")
        sys.exit(1)


if __name__ == "__main__":
    main()
