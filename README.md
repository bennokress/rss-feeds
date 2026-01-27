# RSS Feeds

Unofficial RSS feeds for websites that don't offer their own.

## Available Feeds

<table>
  <tr>
    <td><a href="https://raw.githubusercontent.com/bennokress/rss-feeds/main/Augsburger%20Panther/feed.xml">🔗 Show feed</a></td>
    <td><img src="Augsburger Panther/channel-icon.png" width="50" height="50"></td>
    <td><b><a href="https://www.aev-panther.de/panther/news.html">Augsburger Panther</a></b><br>News from the Augsburger Panther ice hockey team.</td>
  </tr>
</table>

<table>
  <tr>
    <td><a href="https://raw.githubusercontent.com/bennokress/rss-feeds/main/Homey%20App%20Store%20-%20New%20Apps/feed.xml">🔗 Show feed</a></td>
    <td><img src="Homey App Store - New Apps/channel-icon.png" width="50" height="50"></td>
    <td><b><a href="https://homey.app/en-us/apps/homey-pro/">Homey App Store - New Apps</a></b><br>New apps added to the Homey Pro App Store.</td>
  </tr>
</table>

<table>
  <tr>
    <td><a href="https://raw.githubusercontent.com/bennokress/rss-feeds/main/Komood%20Store/feed.xml">🔗 Show feed</a></td>
    <td><img src="Komood Store/channel-icon.png" width="50" height="50"></td>
    <td><b><a href="https://www.komood.store/collections/t-shirt-kollektion">Komood Store</a></b><br>New shirts from Komood Bavarian Apparel.</td>
  </tr>
</table>

## How It Works

Each feed has its own directory containing:

- `scraper.py` - Python script that scrapes the source website
- `articles.tsv` - Database of all discovered articles (for deduplication)
- `feed.xml` - The generated RSS feed

A GitHub Action runs periodically to check for new articles and update the feeds.
