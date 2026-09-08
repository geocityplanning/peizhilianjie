from urllib.parse import parse_qs, urlsplit


def extract_cloud_app_key(cloud_app_link: str) -> str:
    """Return the stable ``i`` value from a cloud-app URL."""
    raw = (cloud_app_link or "").strip()
    if not raw:
        return ""

    parsed = urlsplit(raw)
    queries = [parsed.query]
    if "?" in parsed.fragment:
        queries.append(parsed.fragment.split("?", 1)[1])

    for query in queries:
        values = parse_qs(query, keep_blank_values=True).get("i")
        if values and values[0].strip():
            return values[0].strip()

    if "i=" in raw:
        return raw.split("i=", 1)[1].split("&", 1)[0].strip()
    return raw
