
FORBIDDEN_RESPONSE_HEADERS = {"set-cookie", "content-length", "connection", "transfer-encoding"}


def apply_response_policy(headers: list[tuple[str, str]], policy) -> list[tuple[str, str]]:
    output = {key.lower(): (key, value) for key, value in headers}
    for item in policy.response_headers:
        name = item.header_name.lower()
        if name in FORBIDDEN_RESPONSE_HEADERS or "\r" in (item.header_value or "") or "\n" in (item.header_value or ""):
            continue
        if item.action == "remove":
            output.pop(name, None)
        elif item.action in {"add", "replace"} and item.header_value is not None:
            output[name] = (item.header_name, item.header_value)
    return list(output.values())
