#!/usr/bin/env python3
"""
Scraper for Augsburger Panther news.
Generates an RSS feed from https://www.aev-panther.de/panther/news.html
"""

import argparse
import csv
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from feedgen.feed import FeedGenerator

BASE_URL = "https://www.aev-panther.de"
NEWS_URL = f"{BASE_URL}/panther/news.html"
SCRIPT_DIR = Path(__file__).parent
ARTICLES_FILE = SCRIPT_DIR / "articles.tsv"
FEED_FILE = SCRIPT_DIR / "feed.xml"
MAX_ARTICLES = 50
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


def german_date_to_iso(date_str: str) -> str:
    """Convert DD.MM.YYYY to YYYY-MM-DD."""
    if not date_str:
        return ""
    parts = date_str.split(".")
    if len(parts) == 3:
        day, month, year = parts
        return f"{year}-{month}-{day}"
    return date_str


def parse_news_items(html: str, existing_urls: set[str]) -> list[tuple[str, str, str, str]]:
    """
    Parse news items one by one from top to bottom.
    Stops when a known URL is found or MAX_ARTICLES new items are collected.
    Returns list of (date, time, title, url) tuples with ISO date format.
    """
    soup = parse_html(html)
    articles = []

    for item in soup.select("div.news-item"):
        link = item.select_one("a")
        if not link:
            continue

        url = link.get("href", "")
        if not url.startswith("http"):
            url = BASE_URL + url

        # Stop if we hit a known article
        if url in existing_urls:
            print(f"Found known article, stopping parse")
            break

        # Get all spans inside the news item link
        spans = item.select("div.newsitem_link span")
        if len(spans) >= 2:
            date_text = spans[0].get_text(strip=True)
            title = spans[1].get_text(strip=True)
        else:
            date_text = ""
            title = ""

        # Parse "DD.MM.YYYY | HH:MM Uhr" and convert to ISO date
        date_match = re.match(r"(\d{2}\.\d{2}\.\d{4})\s*\|\s*(\d{2}:\d{2})", date_text)
        if date_match:
            date_str = german_date_to_iso(date_match.group(1))
            time_str = date_match.group(2)
        else:
            date_str = ""
            time_str = ""

        if title and url:
            articles.append((date_str, time_str, title, url))
            print(f"New article: {title}")

            # Stop if we've collected enough new articles
            if len(articles) >= MAX_ARTICLES:
                print(f"Reached {MAX_ARTICLES} new articles, stopping parse")
                break

    return articles


def load_existing_articles() -> list[dict]:
    """
    Load existing articles from TSV file.
    Returns list of article dicts in order (newest first).
    """
    articles = []
    if ARTICLES_FILE.exists():
        with open(ARTICLES_FILE, "r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f, delimiter="\t")
            for row in reader:
                articles.append({
                    "date": row["Date"],
                    "time": row["Time"],
                    "title": row["Title"],
                    "url": row["URL"],
                    "description": row.get("Description", ""),
                    "image": row.get("Image", ""),
                })
    return articles


def save_articles(articles: list[dict]) -> None:
    """Save articles to TSV file."""
    with open(ARTICLES_FILE, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, delimiter="\t")
        writer.writerow(["Date", "Time", "Title", "URL", "Description", "Image"])
        for article in articles:
            writer.writerow([
                article["date"],
                article["time"],
                article["title"],
                article["url"],
                article["description"],
                article["image"],
            ])


def fetch_article_content(url: str) -> tuple[str, str]:
    """
    Fetch article teaser (first paragraph) and image.
    Returns (teaser_text, image_url). Both empty string if not found.
    """
    html = fetch_html(url)
    soup = parse_html(html)

    # Extract first paragraph as teaser
    text = ""
    content_area = soup.select_one("div.contentarea")
    if content_area:
        first_p = content_area.select_one("p")
        if first_p:
            # Get text up to first <br> or <strong> (section header)
            parts = []
            for child in first_p.children:
                if child.name in ("br", "strong"):
                    break
                if hasattr(child, "get_text"):
                    parts.append(child.get_text())
                else:
                    parts.append(str(child))
            text = "".join(parts).strip()
            if text:
                text += " […]"

    # Extract main image from article
    image_url = ""
    img = soup.select_one("div.article_image img")
    if img:
        src = img.get("src", "")
        if src:
            if not src.startswith("http"):
                src = BASE_URL + src
            image_url = src

    return text, image_url


def fetch_article_content_with_retry(url: str) -> tuple[str, str] | None:
    """
    Fetch article content with retry logic.
    Returns (description, image_url) on success, None if all retries fail.
    """
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            description, image = fetch_article_content(url)
            if description:  # Only consider success if we got a description
                return description, image
            print(f"  Attempt {attempt}: No description found")
        except Exception as e:
            print(f"  Attempt {attempt}: {e}")

        if attempt < MAX_RETRIES:
            time.sleep(2)  # Wait before retry

    return None


def parse_date(date_str: str, time_str: str) -> datetime | None:
    """Parse ISO date (YYYY-MM-DD) and time to datetime."""
    if not date_str:
        return None
    try:
        dt_str = f"{date_str} {time_str}" if time_str else date_str
        fmt = "%Y-%m-%d %H:%M" if time_str else "%Y-%m-%d"
        dt = datetime.strptime(dt_str, fmt)
        return dt.replace(tzinfo=TIMEZONE)
    except ValueError:
        return None


def send_webhook(article: dict) -> bool:
    """
    Send article data to webhook.
    Returns True on success, False on failure.
    """
    webhook_url = os.environ.get("MAKE_PANTHER_WEBHOOK_URL")
    token = os.environ.get("MAKE_WEBHOOKS_TOKEN")
    if not webhook_url or not token:
        print("  Warning: MAKE_PANTHER_WEBHOOK_URL or MAKE_WEBHOOKS_TOKEN not set, skipping webhook")
        return False

    # Convert date/time to Unix timestamp
    timestamp = None
    if article["date"]:
        try:
            dt_str = f"{article['date']} {article['time']}" if article["time"] else article["date"]
            fmt = "%Y-%m-%d %H:%M" if article["time"] else "%Y-%m-%d"
            dt = datetime.strptime(dt_str, fmt)
            dt = dt.replace(tzinfo=TIMEZONE)
            timestamp = int(dt.timestamp())
        except ValueError:
            pass

    payload = {
        "title": article["title"],
        "description": article["description"],
        "url": article["url"],
        "imageURL": article["image"],
        "timestamp": timestamp,
    }

    try:
        response = requests.post(
            webhook_url,
            json=payload,
            headers={"x-make-apikey": token},
            timeout=30,
        )
        response.raise_for_status()
        return True
    except Exception as e:
        print(f"  Webhook failed: {e}")
        return False


def generate_rss_feed(articles: list[dict]) -> None:
    """Generate RSS feed from articles."""
    fg = FeedGenerator()
    fg.title("Augsburger Panther")
    fg.link(href=NEWS_URL, rel="alternate")
    fg.description("Aktuelle News der Augsburger Panther. Inoffizieller RSS Feed der Website.")
    fg.language("de")
    fg.ttl(120)
    fg.image(
        url="https://raw.githubusercontent.com/bennokress/rss-feeds/main/Augsburger%20Panther/channel-icon.png",
        title="Augsburger Panther",
        link=NEWS_URL,
    )

    # Only include articles with descriptions
    for article in articles:
        if not article["description"]:
            continue

        fe = fg.add_entry()
        fe.id(article["url"])
        fe.title(article["title"])
        fe.link(href=article["url"])

        pub_date = parse_date(article["date"], article["time"])
        if pub_date:
            fe.pubDate(pub_date)

        fe.description(article["description"])
        if article["image"]:
            fe.enclosure(article["image"], 0, "image/jpeg")

    fg.rss_file(str(FEED_FILE), pretty=True)


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Scrape Augsburger Panther news")
    parser.add_argument("--webhook", action="store_true", help="Send new articles to webhook")
    args = parser.parse_args()

    # Load existing articles
    existing = load_existing_articles()
    existing_urls = {a["url"] for a in existing}
    print(f"Loaded {len(existing)} existing articles")

    # Fetch and parse news page
    print("Fetching news page...")
    html = fetch_html(NEWS_URL)

    print("Parsing new articles...")
    new_articles = parse_news_items(html, existing_urls)
    print(f"Found {len(new_articles)} new articles")

    # Merge: new articles at the top, then existing
    new_article_dicts = [
        {"date": date, "time": time, "title": title, "url": url, "description": "", "image": ""}
        for date, time, title, url in new_articles
    ]
    merged = new_article_dicts + existing

    # Trim to MAX_ARTICLES
    merged = merged[:MAX_ARTICLES]
    print(f"Total articles after trim: {len(merged)}")

    # Fetch content for articles missing description
    articles_needing_content = [a for a in merged if not a["description"]]
    updated_articles = []

    if articles_needing_content:
        print(f"\nFetching content for {len(articles_needing_content)} articles...")
        for article in articles_needing_content:
            print(f"Fetching: {article['title']}")
            result = fetch_article_content_with_retry(article["url"])
            if result:
                description, image = result
                article["description"] = description
                article["image"] = image
                updated_articles.append(article)
                print("  Success")
            else:
                print(f"  Failed after {MAX_RETRIES} attempts")

        print(f"\nSuccessfully fetched {len(updated_articles)}/{len(articles_needing_content)} articles")

    # Send webhooks for new articles (if enabled)
    if args.webhook and updated_articles:
        print(f"\nSending webhooks for {len(updated_articles)} new articles...")
        for article in updated_articles:
            print(f"  Webhook: {article['title']}")
            if send_webhook(article):
                print("    Sent")

    # Save articles
    save_articles(merged)

    # Generate feed
    with_content = sum(1 for a in merged if a["description"])
    print(f"\nGenerating RSS feed ({with_content} articles with content)...")
    generate_rss_feed(merged)
    print(f"Feed saved to {FEED_FILE}")

    # Output summary for commit message
    if updated_articles:
        print("\n--- COMMIT_SUMMARY ---")
        for article in updated_articles:
            print(f"{article['date']} {article['title']}")
        print("--- END_SUMMARY ---")
        sys.exit(0)
    else:
        print("\nNo new content to commit")
        sys.exit(1)  # Signal to workflow: skip commit


if __name__ == "__main__":
    main()
