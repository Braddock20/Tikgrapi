"""TikTok web client constants and defaults."""

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
)

API_BASE = "https://www.tiktok.com"
ROOT_REFERER = "https://www.tiktok.com/"

AID = "1988"
APP_NAME = "tiktok_web"
CHANNEL = "tiktok_web"
DEVICE_PLATFORM = "web_pc"
OS = "mac"
BROWSER_NAME = "Mozilla"
BROWSER_LANGUAGE = "en-GB"
APP_LANGUAGE = "en-GB"
WEBCAST_LANGUAGE = "en-GB"
BROWSER_PLATFORM = "MacIntel"

REGION = "NG"
PRIORITY_REGION = "NG"
TZ_NAME = "Africa/Lagos"

SCREEN_WIDTH = 1680
SCREEN_HEIGHT = 1050

IMPERSONATE = "chrome131"

SIG_PARAMS = ("msToken", "X-Bogus", "X-Gnarly")

DEFAULT_TIMEOUT = 20

# Endpoints in TikTok bundle's enablePathList — wrapper auto-signs these
SIGNED_PATHS_PREFIX = "/api"

# Browser version string (decoded — urlencode handles encoding)
BROWSER_VERSION = (
    "5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/147.0.0.0 Safari/537.36"
)
