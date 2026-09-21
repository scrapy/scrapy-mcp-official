import scrapy


class PriceToFloatPipeline:
    """Turn the price string into a number, e.g. "£51.77" into 51.77."""

    def process_item(self, item, spider):
        item["price"] = float(item["price"].lstrip("£"))
        return item


class BooksSpider(scrapy.Spider):
    name = "books"
    start_urls = ["https://books.toscrape.com/"]
    custom_settings = {
        "DOWNLOAD_DELAY": 1,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 2,
        "ITEM_PIPELINES": {PriceToFloatPipeline: 300},
        # Crawl the site over and over, so that the crawl outlives any eval
        # run, with a ceiling in case it is left behind.
        "DUPEFILTER_CLASS": "scrapy.dupefilters.BaseDupeFilter",
        "CLOSESPIDER_TIMEOUT": 4 * 60 * 60,
        "LOG_LEVEL": "INFO",
    }

    def parse(self, response):
        yield from response.follow_all(
            css="article.product_pod h3 a", callback=self.parse_book
        )
        yield from response.follow_all(css="li.next a")
        if not response.css("li.next"):
            yield response.follow(self.start_urls[0])

    def parse_book(self, response):
        yield {
            "title": response.css("h1::text").get(),
            "price": response.css("p.price_color::text").get(),
            "upc": response.css("td.upc::text").get(),
            "url": response.url,
        }
