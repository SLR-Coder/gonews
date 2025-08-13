# -*- coding: utf-8 -*-
"""
Kategori -> [(Kaynak Adı, RSS URL)]
Bu dosyayı dilediğin zaman düzenleyebilirsin; robotlar buradan okuyor.
"""

FEEDS = {
    "spor": [
        ("BBC Sport Genel", "https://feeds.bbci.co.uk/sport/rss.xml?edition=uk"),
        ("BBC Sport Futbol", "https://feeds.bbci.co.uk/sport/football/rss.xml?edition=uk"),
        ("Sky Sports Genel", "https://www.skysports.com/rss/12040"),
        ("Sky Sports Futbol", "https://www.skysports.com/rss/11095"),
    ],
    "ekonomi": [
        ("Financial Times", "https://www.ft.com/?format=rss"),
    ],
    "teknoloji": [
        ("TechCrunch", "https://feeds.feedburner.com/TechCrunch/"),
        ("Engadget", "https://www.engadget.com/rss.xml"),
    ],
    "kultur": [
        ("Smithsonian Magazine", "https://www.smithsonianmag.com/rss/latest_articles/"),
        ("Hyperallergic", "https://hyperallergic.com/feed/"),
        ("Dezeen Magazine", "https://www.dezeen.com/feed/"),
    ],
    "dunya_gundemi": [
        ("BBC World News", "https://feeds.bbci.co.uk/news/world/rss.xml"),
        ("The Guardian World", "https://www.theguardian.com/world/rss"),
    ],
    "turkiye_gundemi": [
        ("NTV Gündem", "https://www.ntv.com.tr/gundem.rss"),
        ("NTV Türkiye", "https://www.ntv.com.tr/turkiye.rss"),
    ],
    "sondakika": [
        ("NTV Son Dakika", "https://www.ntv.com.tr/son-dakika.rss"),
    ],
}
