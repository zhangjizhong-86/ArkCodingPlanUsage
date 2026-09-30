#!/usr/bin/env python3
# <bitbar.title>Volcengine Ark Usage</bitbar.title>
# <bitbar.author>YourName</bitbar.author>

"""SwiftBar 插件：菜单栏单行轮换展示火山方舟 Coding Plan 的小时 / 周 / 月度用量。

菜单栏标题一行，在小时 / 周 / 月度三档用量之间轮换。
SwiftBar 每 4 秒执行一次本脚本（由文件名 ark_usage.4s.py 决定），与轮换周期
相等，因此每次刷新都会切到下一档，不做无变化的空渲染。脚本拆成两条路径：

热路径：只读纯文本缓存并打印，不导入 json / pathlib，单次 CPU 约 25ms；
冷路径：缓存过期、上次请求失败退避结束、或配置文件被改动时才走，
        这时才导入 json / urllib 请求接口，并把结果写回纯文本缓存。
"""

import os
import sys
import time

CONFIG_FILE = os.path.join(os.path.expanduser("~"), ".config", "ark_config.json")
CACHE_FILE = os.path.join(os.path.expanduser("~"), ".config", "ark_usage_cache.txt")
URL = "https://console.volcengine.com/api/top/ark/cn-beijing/2024-01-01/GetCodingPlanUsage?"
CONSOLE_URL = "https://console.volcengine.com/ark/region:ark+cn-beijing/openManagement?LLM=%7B%7D&advancedActiveKey=subscribe"

COOKIE_WARN_DAYS = 7
COOKIE_CRIT_DAYS = 14

# 菜单栏轮换周期：每 N 秒切换一个用量，一轮 3 × N 秒；与文件名里的刷新周期保持一致
ROTATE_SECONDS = 4
# 菜单栏标题字体：系统默认字体（SF Pro）的数字是比例宽度，"1" 比 "0" 窄，
# 空格的宽度也不等于数字，所以靠补空格对齐是无效的；实测同一个标题在不同
# 数值间宽度会差 8pt 左右。换成等宽字体 Menlo 后，字符数相同的标题宽度完全一致。
TITLE_FONT = "Menlo"
TITLE_SIZE = 12
# 百分比右对齐到固定字符数：只占 2 位，宽度才不会随数值跳动；
# 到 100% 会多占一位，因此封顶显示 99（下拉菜单里仍是真实值）
PCT_DIGITS = 2
PCT_MAX = 99
# 接口结果缓存有效期，避免 SwiftBar 每次刷新都打接口（60 秒 = 15 次刷新）
API_CACHE_TTL = 60
# 请求失败后的重试间隔，避免刷新时反复打接口
API_RETRY_COOLDOWN = 30
# 配置文件 mtime 的比较容差（秒），远大于一次点击的间隔，足以识别配置改动
CONFIG_MTIME_TOLERANCE = 0.5

LEVEL_ORDER = ("session", "weekly", "monthly")
LEVEL_LABELS = {"session": "小时用量", "weekly": "周用量", "monthly": "月度用量"}
LEVEL_SHORT = {"session": "时", "weekly": "周", "monthly": "月"}

SCRIPT_PATH = os.path.abspath(__file__)
MENU_UPDATE_COOKIE = f"从剪贴板更新 Cookie | shell={SCRIPT_PATH} param1=update param2=cookie terminal=false refresh=true"


# ---------- 配置（冷路径，需要 json） ----------

def load_config():
    try:
        import json

        with open(CONFIG_FILE, encoding="utf-8") as fh:
            return json.load(fh)
    except OSError:
        return {}
    except ValueError:
        return {}


def save_config(cfg):
    import json

    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(cfg, ensure_ascii=False, indent=2))


# ---------- 纯文本缓存（热路径，不依赖 json） ----------
# 格式：key=value 逐行，用量为 level=percent|reset_timestamp
#   written_at=1755000000.000
#   config_mtime=1754000000.000
#   bar_style=dots
#   error=
#   session=38.5000|1755003600

CACHE_DEFAULT = {
    "written_at": 0.0,
    "config_mtime": -1.0,
    "bar_style": "dots",
    "error": "",
}


def read_cache():
    cache = dict(CACHE_DEFAULT)
    for level in LEVEL_ORDER:
        cache[level] = None
    try:
        with open(CACHE_FILE, encoding="utf-8") as fh:
            content = fh.read()
    except OSError:
        return cache

    for line in content.splitlines():
        key, sep, value = line.partition("=")
        if not sep:
            continue
        if key in LEVEL_ORDER:
            percent, _, reset = value.partition("|")
            try:
                cache[key] = (float(percent), float(reset or 0))
            except ValueError:
                continue
        elif key in ("written_at", "config_mtime"):
            try:
                cache[key] = float(value)
            except ValueError:
                pass
        elif key == "bar_style":
            cache[key] = value
        elif key == "error":
            cache[key] = value
    return cache


def write_cache(cache):
    lines = [
        f"written_at={cache['written_at']:.3f}",
        f"config_mtime={cache['config_mtime']:.3f}",
        f"bar_style={cache.get('bar_style', 'dots')}",
        "error=" + str(cache.get("error", "")).replace("\n", " "),
    ]
    for level in LEVEL_ORDER:
        item = cache.get(level)
        if item:
            lines.append(f"{level}={item[0]:.4f}|{item[1]:.0f}")
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    except OSError:
        pass


def file_mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return -1.0


# ---------- Cookie 与剪贴板 ----------

def cookie_age_indicator(age):
    if age is None:
        return ""
    if age >= COOKIE_CRIT_DAYS:
        return "🔴"
    if age >= COOKIE_WARN_DAYS:
        return "⚠️"
    return ""


def parse_curl_from_clipboard():
    import re
    import subprocess

    try:
        text = subprocess.check_output(["pbpaste"], text=True, stderr=subprocess.DEVNULL)
    except Exception:
        return None, None

    # Parse -b '...' or -b "..."
    cookie = None
    for pat in [r"-b\s+'([^']*)'", r'-b\s+"([^"]*)"', r"--cookie\s+'([^']*)'", r'--cookie\s+"([^"]*)"']:
        m = re.search(pat, text)
        if m:
            cookie = m.group(1).strip()
            break
    if not cookie:
        m = re.search(r"Cookie:\s*(.+)", text, re.IGNORECASE)
        if m:
            cookie = m.group(1).strip()

    # Parse x-web-id header
    web_id = None
    m = re.search(r"-H\s+'x-web-id:\s*([^']+)'", text, re.IGNORECASE)
    if not m:
        m = re.search(r'-H\s+"x-web-id:\s*([^"]+)"', text, re.IGNORECASE)
    if m:
        web_id = m.group(1).strip()

    return cookie, web_id


def update_from_clipboard():
    cookie, web_id = parse_curl_from_clipboard()
    if not cookie:
        return False
    cfg = load_config()
    cfg["cookie"] = cookie
    if web_id:
        cfg["web_id"] = web_id
    save_config(cfg)
    # 配置文件 mtime 变化会让下一次刷新立刻用新 Cookie 请求接口
    return True


# ---------- 接口请求（冷路径） ----------

class UsageError(Exception):
    """用量获取失败，message 可直接展示给用户。"""


def extract_csrf_token(cookie):
    import re

    m = re.search(r"csrfToken=([a-f0-9]+)", cookie)
    return m.group(1) if m else None


def fetch_quota(cookie, web_id=None):
    """请求接口，成功返回 {level: (percent, reset_ts)}，失败抛出 UsageError。"""
    import json
    import ssl
    from urllib.error import HTTPError, URLError
    from urllib.request import Request, urlopen

    csrf_token = extract_csrf_token(cookie)
    headers = {
        "accept": "application/json, text/plain, */*",
        "content-type": "application/json",
        "origin": "https://console.volcengine.com",
        "referer": CONSOLE_URL,
        "user-agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
        ),
        "Cookie": cookie,
    }
    if csrf_token:
        headers["x-csrf-token"] = csrf_token
    if web_id:
        headers["x-web-id"] = web_id

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    req = Request(URL, data=b"{}", headers=headers, method="POST")
    try:
        with urlopen(req, timeout=10, context=ctx) as response:
            resp = json.loads(response.read().decode("utf-8"))
    except HTTPError as e:
        raise UsageError("Cookie 过期" if e.code in (401, 403) else f"HTTP {e.code}")
    except (URLError, OSError) as e:
        raise UsageError(f"网络错误：{e}")
    except ValueError:
        raise UsageError("接口返回数据异常")

    quota = resp.get("Result", {}).get("QuotaUsage", [])
    records = {}
    for item in quota:
        level = item.get("Level")
        if level in LEVEL_ORDER and level not in records:
            records[level] = (float(item.get("Percent", 0)), float(item.get("ResetTimestamp") or 0))
    if not records:
        message = resp.get("ResponseMetadata", {}).get("Error", {}).get("Message", "未找到用量数据")
        raise UsageError(f"接口返回：{message}")
    return records


def refresh_cache(config_mtime, prev):
    """冷路径：读配置、按需请求接口，返回并落盘新的缓存。"""
    cache = dict(prev)
    cache["config_mtime"] = config_mtime
    cfg = load_config()
    cache["bar_style"] = cfg.get("bar_style", "dots") or "dots"
    cookie = cfg.get("cookie")

    if not cookie:
        cache["written_at"] = time.time()
        cache["error"] = "未配置 Cookie"
        write_cache(cache)
        return cache

    try:
        records = fetch_quota(cookie, cfg.get("web_id"))
    except UsageError as e:
        cache["written_at"] = time.time()
        cache["error"] = str(e)
        write_cache(cache)
        return cache

    cache["written_at"] = time.time()
    cache["error"] = ""
    for level in LEVEL_ORDER:
        cache[level] = records.get(level)
    write_cache(cache)
    return cache


def resolve_cache(now):
    """返回 (cache, err)。err 非空说明展示的是上次缓存数据。"""
    config_mtime = file_mtime(CONFIG_FILE)
    cache = read_cache()

    if cache["written_at"] <= 0:
        cache = refresh_cache(config_mtime, cache)
    else:
        window = API_RETRY_COOLDOWN if cache["error"] else API_CACHE_TTL
        # 配置文件被改动（更新 Cookie 或切换进度条样式）时立刻重新取值；
        # mtime 落盘时有精度损失，必须用容差比较，否则会误判成“配置已变更”。
        config_changed = abs(config_mtime - cache["config_mtime"]) > CONFIG_MTIME_TOLERANCE
        if now - cache["written_at"] >= window or config_changed:
            cache = refresh_cache(config_mtime, cache)

    err = cache["error"] or None
    age = (now - config_mtime) / 86400 if config_mtime > 0 else None
    return cache, err, age


# ---------- 展示 ----------

def render_bar(percent, width=5):
    total = width * 2
    filled = round(percent / 100 * total)
    full = filled // 2
    half = filled % 2
    return "●" * full + ("◐" if half else "") + "○" * (width - full - half)


def render_bar_blocks(percent, width=5):
    total = width * 2
    filled = round(percent / 100 * total)
    full = filled // 2
    half = filled % 2
    return "■" * full + ("◧" if half else "") + "□" * (width - full - half)


def format_reset(reset_ts, now):
    diff = reset_ts - now
    if diff <= 0:
        return "即将刷新"
    days = int(diff // 86400)
    hours = int((diff % 86400) // 3600)
    minutes = int((diff % 3600) // 60)
    parts = []
    if days > 0:
        parts.append(f"{days}天")
    if hours > 0 or days > 0:
        parts.append(f"{hours}小时")
    parts.append(f"{minutes}分钟")
    return "".join(parts)


def get_usage():
    now = time.time()
    cache, err, age = resolve_cache(now)
    age_flag = cookie_age_indicator(age)
    records = [(level, cache[level]) for level in LEVEL_ORDER if cache.get(level)]

    if not records:
        message = err or "无用量数据"
        print(f"{message}{age_flag}")
        print("---")
        print("火山方舟 Coding Plan 用量")
        print(f"错误: {message}")
        if age is None:
            print(f"配置文件: {CONFIG_FILE}")
        print(MENU_UPDATE_COOKIE)
        print(f"打开火山控制台 | href={CONSOLE_URL}")
        return

    idx = int(now // ROTATE_SECONDS) % len(records)
    stale_flag = "⚠️" if err else ""

    # 标题单行轮换；等宽字体 + 固定字符数占位，保证任何数值下宽度都不变
    level, (pct, _reset) = records[idx]
    pct_text = f"{min(pct, PCT_MAX):>{PCT_DIGITS}.0f}%"
    print(
        f"{LEVEL_SHORT[level]} {pct_text}{stale_flag}{age_flag}"
        f" | font={TITLE_FONT} size={TITLE_SIZE}"
    )
    print("---")
    print("火山方舟 Coding Plan 用量")

    bar_style = cache.get("bar_style", "dots")
    bar_fn = render_bar_blocks if bar_style == "blocks" else render_bar

    cur_level = records[idx][0]
    for level, (pct, _reset) in records:
        label = LEVEL_LABELS.get(level, level)
        marker = "▶ " if level == cur_level else "   "
        print(f"{marker}{label}: {bar_fn(pct)} {pct:.1f}%")

    print("---")
    # 三档用量的刷新倒计时固定全部展示，不随菜单栏轮换变化
    for level, (_pct, reset) in records:
        label = LEVEL_LABELS.get(level, level)
        if reset <= 0:
            print(f"{label}无固定刷新时间")
        else:
            print(f"{label}距刷新还剩 {format_reset(reset, now)}")
    cycle = " → ".join(LEVEL_SHORT[level] for level, _ in records)
    print(f"菜单栏每 {ROTATE_SECONDS} 秒轮换：{cycle}")

    if err:
        # Cookie 年龄并入错误行，避免下拉里出现两行同义提示
        if age is not None and age >= COOKIE_WARN_DAYS:
            icon = "🔴" if age >= COOKIE_CRIT_DAYS else "⚠️"
            print(f"{icon} {err}（凭据已 {age:.0f} 天未更新）")
        else:
            print(f"⚠️ {err}")

    print("---")
    if age is not None and not err:
        if age >= COOKIE_CRIT_DAYS:
            print(f"🔴 Cookie 已 {age:.0f} 天未更新，可能即将过期")
        elif age >= COOKIE_WARN_DAYS:
            print(f"⚠️ Cookie 已 {age:.0f} 天未更新")

    if bar_style == "blocks":
        print(f"切换为圆点进度条 | shell={SCRIPT_PATH} param1=toggle param2=bar-style terminal=false refresh=true")
    else:
        print(f"切换为方块进度条 | shell={SCRIPT_PATH} param1=toggle param2=bar-style terminal=false refresh=true")

    print(MENU_UPDATE_COOKIE)
    print(f"打开火山控制台 | href={CONSOLE_URL}")


def toggle_bar_style():
    cfg = load_config()
    current = cfg.get("bar_style", "dots")
    new = "blocks" if current == "dots" else "dots"
    cfg["bar_style"] = new
    save_config(cfg)
    label = "方块" if new == "blocks" else "圆点"
    print(f"已切换为{label}进度条")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "update" and len(sys.argv) > 2 and sys.argv[2] == "cookie":
        if update_from_clipboard():
            print("Cookie 已更新")
        else:
            print("剪贴板中未找到有效 Cookie")
            print("请先在浏览器 F12 → Network → 复制 curl 命令")
        sys.exit(0)

    if len(sys.argv) > 1 and sys.argv[1] == "toggle" and len(sys.argv) > 2 and sys.argv[2] == "bar-style":
        toggle_bar_style()
        sys.exit(0)

    get_usage()
