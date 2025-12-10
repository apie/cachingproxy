from quart import (
    Quart,
    request,
    render_template_string,
    redirect,
    url_for,
    Response,
)
import aiofiles
import aiohttp
from bs4 import BeautifulSoup
import os
import hashlib
import mimetypes
import urllib.parse
from urllib.parse import urlparse, urljoin
import time
import pathlib

# written with help of Claude.ai

app = Quart(__name__)

# Create cache directory if it doesn't exist
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")
os.makedirs(CACHE_DIR, exist_ok=True)

PREFIX = '/cachingproxy'
ALLOW_URLS = ["http://example.com", "https://adventofcode.com"]
ONE_MINUTE_IN_SECONDS = 60
ONE_HOUR_IN_SECONDS = 60 * ONE_MINUTE_IN_SECONDS
ONE_DAY_IN_SECONDS = 24 * ONE_HOUR_IN_SECONDS
ONE_YEAR_IN_SECONDS = 365 * ONE_DAY_IN_SECONDS
DEFAULT_CACHE_EXPIRY = ONE_YEAR_IN_SECONDS

def readable(num_seconds: int) -> str:
    if num_seconds == ONE_DAY_IN_SECONDS:
        return "one day"
    if num_seconds == ONE_YEAR_IN_SECONDS:
        return "one year"
    return f"{num_seconds} seconds"

def encode_url(url):
    """https://stackoverflow.com/questions/66926813/use-url-as-filename"""
    return url.replace("/", "$").replace(":", "#")


def get_cache_path(url, content_type=None):
    """Generate a cache file path based on URL"""
    url_hash = hashlib.md5(url.encode()).hexdigest()
    parsed = urlparse(url)
    url_hash = encode_url(url)

    ext = ".html"
    if content_type and "image" in content_type:
        # Extract extension from content type or URL
        ext = (
            mimetypes.guess_extension(content_type) or os.path.splitext(parsed.path)[1]
        )
        if not ext:
            ext = ".bin"  # Default extension if we can't determine it
    fpath = os.path.join(CACHE_DIR, parsed.netloc, f"{url_hash}{ext}")
    pathlib.Path(os.path.join(CACHE_DIR, parsed.netloc)).mkdir(exist_ok=True)
    return fpath


def is_cached(url, max_age=DEFAULT_CACHE_EXPIRY):
    """Check if URL is cached and not expired. Max age in seconds."""
    # TODO could also use Last-Modified header
    cache_path = get_cache_path(url)
    if os.path.exists(cache_path):
        # Check if cache is fresher than max_age
        if time.time() - os.path.getmtime(cache_path) < max_age:
            print("YES cached:", url)
            return True
    print("NOT cached:", url)
    return False


async def read_cache(cache_path, binary=False):
    """Read content from cache asynchronously"""
    if binary:
        async with aiofiles.open(cache_path, "rb") as f:
            return await f.read()
    else:
        async with aiofiles.open(
            cache_path, "r", encoding="utf-8", errors="replace"
        ) as f:
            return await f.read()


async def write_cache(cache_path, content, binary=False):
    """Write content to cache asynchronously"""
    if binary:
        async with aiofiles.open(cache_path, "wb") as f:
            await f.write(content)
    else:
        async with aiofiles.open(cache_path, "w", encoding="utf-8") as f:
            await f.write(content)


def rewrite_url(url, base_url):
    """Rewrite a URL to go through the proxy"""
    if not url:
        return url

    # Skip rewriting for javascript: and data: URLs
    if url.startswith(("javascript:", "data:", "#", "mailto:")):
        return url

    # Handle relative URLs
    if not url.startswith(("http://", "https://")):
        # Convert to absolute URL based on base_url
        return f"{PREFIX}/proxy?url={urllib.parse.quote(urljoin(base_url, url))}"

    # Absolute URLs
    return f"{PREFIX}/proxy?url={urllib.parse.quote(url)}"


def rewrite_html(html_content, base_url):
    """Rewrite HTML content to make all links go through the proxy"""
    if not html_content:
        return ""

    soup = BeautifulSoup(html_content, "html.parser")

    # Rewrite links (a href)
    for a in soup.find_all("a", href=True):
        a["href"] = rewrite_url(a["href"], base_url)

    # Rewrite image sources
    for img in soup.find_all("img", src=True):
        img["src"] = rewrite_url(img["src"], base_url)
        # Also handle srcset if present
        if img.get("srcset"):
            srcsets = []
            for srcset in img["srcset"].split(","):
                parts = srcset.strip().split(" ")
                if len(parts) >= 1:
                    parts[0] = rewrite_url(parts[0], base_url)
                srcsets.append(" ".join(parts))
            img["srcset"] = ", ".join(srcsets)

    # Rewrite CSS links
    for link in soup.find_all("link", href=True):
        if link.get("rel") and "stylesheet" in link.get("rel"):
            link["href"] = rewrite_url(link["href"], base_url)

    # Rewrite script sources
    for script in soup.find_all("script", src=True):
        script["src"] = rewrite_url(script["src"], base_url)

    # Rewrite form actions
    for form in soup.find_all("form", action=True):
        form["action"] = rewrite_url(form["action"], base_url)

    # Add base target to keep everything in the proxy
    base_tag = soup.find("base") or soup.new_tag("base")
    base_tag["target"] = "_self"
    meta_tag = soup.new_tag("meta")
    meta_tag["name"] = "viewport"
    meta_tag["content"] = "width=device-width, initial-scale=1.0"
    stylesheet2 = soup.find("link", attrs={
        "rel": "stylesheet alternate",
    })
    if stylesheet2:
        stylesheet = soup.find("link", attrs={
            "rel": "stylesheet",
        })
        stylesheet["rel"] = "ONZIN"  # Make invalid
        stylesheet2["rel"] = "stylesheet"  # Make default
    if not soup.find("base"):
        if soup.head:
            soup.head.insert(0, base_tag)
            soup.head.append(meta_tag)
        elif soup.html:
            head = soup.new_tag("head")
            head.append(base_tag)
            soup.html.insert(0, head)

    return str(soup)


@app.route(f"{PREFIX}/")
async def index():
    """Display the form to enter a URL"""
    template = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Async Web Proxy Caching Tool</title>
        <style>
            body {{
                font-family: Arial, sans-serif;
                max-width: 800px;
                margin: 0 auto;
                padding: 20px;
                line-height: 1.6;
            }}
            h1 {{
                color: #333;
            }}
            form {{
                background: #f8f8f8;
                padding: 20px;
                border-radius: 5px;
                margin: 20px 0;
            }}
            input[type="url"] {{
                width: 80%;
                padding: 10px;
                border: 1px solid #ddd;
                border-radius: 4px;
            }}
            button {{
                background: #4CAF50;
                color: white;
                border: none;
                padding: 10px 15px;
                border-radius: 4px;
                cursor: pointer;
            }}
            button:hover {{
                background: #45a049;
            }}
            .info {{
                background: #e7f3ff;
                padding: 15px;
                border-radius: 5px;
                margin-bottom: 20px;
            }}
        </style>
    </head>
    <body>
        <h1>Async Web Proxy Caching Tool</h1>
        
        <div class="info">
            Enter a URL to view through the proxy. The page will be cached ({readable(DEFAULT_CACHE_EXPIRY)}) for faster subsequent access.
            Note: Only a small number of urls are allowed!
        </div>
        
        <form action="{PREFIX}/proxy" method="get">
            <input type="url" name="url" placeholder="https://example.com" required>
            <button type="submit">Load URL</button>
        </form>
    </body>
    </html>
    """
    return await render_template_string(template)


@app.route(f"{PREFIX}/proxy")
async def proxy_full_async():
    """Fully asynchronous version of the proxy"""
    url = request.args.get("url")

    if not url:
        return redirect(url_for("index"))

    try:
        # Check URL format and add scheme if missing
        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        allowed = True
        # print(f"{url=}")
        for allowed_url in ALLOW_URLS:
            allowed = False
            if url.startswith(allowed_url):
                allowed = True
                break

        if not allowed:
            raise Exception("Forbidden URL!")
        # Check if content is cached
        is_cached_content = is_cached(url)
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }

        if is_cached_content:
            # Use appropriate cache path
            cache_path = get_cache_path(url)
            if os.path.exists(cache_path):
                if cache_path.endswith('.html'):
                    # If it's HTML, we need to read and process it
                    content = await read_cache(cache_path)
                    return content
                else:
                    # If it's an image or other binary content
                    content = await read_cache(cache_path, binary=True)
                    return Response(content, content_type='image')


        # Not cached or cache expired, fetch the content
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                # Check for successful response
                if response.status != 200:
                    return await render_template_string(
                        """
                    <html>
                    <head>
                        <title>Error</title>
                        <style>
                            body { font-family: Arial, sans-serif; margin: 40px; line-height: 1.6; }
                            .error { background: #ffeeee; padding: 20px; border-radius: 5px; }
                            a { color: #0066cc; }
                        </style>
                    </head>
                    <body>
                        <h1>Error Fetching URL</h1>
                        <div class="error">
                            <p>There was an error fetching the requested URL: {{ url }}</p>
                            <p>Status Code: {{ status_code }}</p>
                        </div>
                        <p><a href="/">Back to home</a></p>
                    </body>
                    </html>
                    """,
                        url=url,
                        status_code=response.status,
                    )

                content_type = response.headers.get("content-type", "").lower()
                cache_path = get_cache_path(url, content_type)

                # Handle binary content (images, etc.)
                if "text/html" not in content_type:
                    # Get binary content
                    content = await response.read()

                    # Save to cache
                    await write_cache(cache_path, content, binary=True)

                    # Return the response
                    return Response(content, content_type=content_type)

                # Handle HTML content
                html_content = await response.text()
                processed_html = rewrite_html(html_content, url)

                # Save to cache
                await write_cache(cache_path, processed_html)

                return processed_html

    except Exception as e:
        return await render_template_string(
            """
        <html>
        <head>
            <title>Error</title>
            <style>
                body { font-family: Arial, sans-serif; margin: 40px; line-height: 1.6; }
                .error { background: #ffeeee; padding: 20px; border-radius: 5px; }
                a { color: #0066cc; }
            </style>
        </head>
        <body>
            <h1>Error Fetching URL</h1>
            <div class="error">
                <p>There was an error fetching the requested URL: {{ url }}</p>
                <p>Error: {{ error }}</p>
            </div>
            <p><a href="/">Back to home</a></p>
        </body>
        </html>
        """,
            url=url,
            error=str(e),
        )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
