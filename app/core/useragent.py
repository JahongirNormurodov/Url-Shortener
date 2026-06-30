"""User-Agent parser — device, browser va OS aniqlash.

ua-parser yoki user-agents kutubxonasi ishlatiladi.
Ikkalasi ham yo'q bo'lsa — sodda regex fallback.

O'rnatish:
    pip install user-agents
"""

import logging
import re

logger = logging.getLogger(__name__)


def parse_user_agent(ua_string: str | None) -> dict:
    """User-Agent stringdan device_type, browser, os ajratadi.

    Qaytaradi:
        {
            "device_type": "mobile" | "desktop" | "tablet" | "bot",
            "browser": "Chrome" | "Safari" | ...,
            "os": "Android" | "iOS" | "Windows" | "macOS" | "Linux" | ...
        }
    """
    if not ua_string:
        return {"device_type": None, "browser": None, "os": None}

    try:
        from user_agents import parse  # type: ignore
        ua = parse(ua_string)

        if ua.is_bot:
            device_type = "bot"
        elif ua.is_tablet:
            device_type = "tablet"
        elif ua.is_mobile:
            device_type = "mobile"
        else:
            device_type = "desktop"

        browser = ua.browser.family or None
        os_name = ua.os.family or None

        return {"device_type": device_type, "browser": browser, "os": os_name}

    except ImportError:
        logger.debug("[useragent] user-agents kutubxonasi yo'q, regex fallback ishlatiladi")
        return _regex_parse(ua_string)


def _regex_parse(ua: str) -> dict:
    """Sodda regex asosidagi fallback parser."""
    ua_lower = ua.lower()

    # Bot aniqlash
    bot_patterns = ["bot", "crawler", "spider", "slurp", "googlebot", "bingbot"]
    if any(p in ua_lower for p in bot_patterns):
        return {"device_type": "bot", "browser": None, "os": None}

    # Device type
    if re.search(r"tablet|ipad|playbook|silk", ua_lower):
        device_type = "tablet"
    elif re.search(r"mobile|android|iphone|ipod|blackberry|windows phone", ua_lower):
        device_type = "mobile"
    else:
        device_type = "desktop"

    # Browser
    if "edg" in ua_lower:
        browser = "Edge"
    elif "opr" in ua_lower or "opera" in ua_lower:
        browser = "Opera"
    elif "firefox" in ua_lower:
        browser = "Firefox"
    elif "chrome" in ua_lower:
        browser = "Chrome"
    elif "safari" in ua_lower:
        browser = "Safari"
    else:
        browser = "Other"

    # OS
    if "windows" in ua_lower:
        os_name = "Windows"
    elif "android" in ua_lower:
        os_name = "Android"
    elif "iphone" in ua_lower or "ipad" in ua_lower or "ipod" in ua_lower:
        os_name = "iOS"
    elif "mac os" in ua_lower or "macos" in ua_lower:
        os_name = "macOS"
    elif "linux" in ua_lower:
        os_name = "Linux"
    else:
        os_name = "Other"

    return {"device_type": device_type, "browser": browser, "os": os_name}
