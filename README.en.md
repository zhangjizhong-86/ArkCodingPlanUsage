# Ark Coding Plan Usage (SwiftBar Plugin)

English | [简体中文](README.md)

Monitor your **Volcengine Ark Coding Plan** session / weekly / monthly usage right from the macOS menu bar.

A single menu bar line rotates between the three levels every 4 seconds; the dropdown shows progress bars, reset countdowns, and quick actions.

- This repo (fork): <https://github.com/zhangjizhong-86/ArkCodingPlanUsage>
- Upstream author: <https://github.com/xiaokaiyyy/ArkCodingPlanUsage>

> This fork makes two key changes over upstream: the menu bar **rotates through all three levels** instead of showing only the highest one, and it adds **a local cache with hot/cold code paths so it does not hit the API on every refresh**. See [Differences from upstream](#differences-from-upstream).

---

## Table of Contents

- [Preview](#preview)
- [Requirements & Installation](#requirements--installation)
- [Configuring the Cookie](#configuring-the-cookie)
- [Menu Bar Rotation Strategy](#menu-bar-rotation-strategy)
- [Performance Impact](#performance-impact)
- [Dropdown Menu](#dropdown-menu)
- [Configuration](#configuration)
- [Differences from upstream](#differences-from-upstream)
- [FAQ](#faq)
- [License](#license)

---

## Preview

The menu bar shows one line, rotating through the three levels (4 seconds each):

```
时 23%      →      周  4%      →      月 26%      →      (loop)
```

Dropdown menu:

```
火山方舟 Coding Plan 用量
────────────────────────────────
▶ 小时用量: ●○○○○ 23.4%
   周用量:   ○○○○○  4.1%
   月度用量: ●◐○○○ 26.3%
────────────────────────────────
小时用量距刷新还剩 3小时12分钟
周用量距刷新还剩 5天4小时
月度用量距刷新还剩 12天6小时
菜单栏每 4 秒轮换：时 → 周 → 月
────────────────────────────────
切换为方块进度条
从剪贴板更新 Cookie
打开火山控制台
```

> The UI strings are in Chinese (hourly / weekly / monthly usage), matching the Volcengine Ark console.

---

## Requirements & Installation

**Requirements**

- macOS
- [SwiftBar](https://github.com/swiftbar/SwiftBar) (recommended, `brew install --cask swiftbar`) or BitBar
- Python 3 (standard library only, no third-party dependencies)

**Steps**

1. Install and launch SwiftBar, then set a plugin folder (e.g. `~/Library/Application Support/SwiftBar/Plugins`).
2. Put `ark_usage.4s.py` into that folder and make it executable:

   ```bash
   chmod +x ark_usage.4s.py
   ```

3. Update the shebang on the first line to point at your own Python interpreter:

   ```python
   #!/usr/bin/env python3
   # or an absolute path, e.g.:
   #!/opt/homebrew/bin/python3
   ```

4. SwiftBar reads the refresh interval from the file name (`.4s.` = every 4 seconds) and runs it immediately.

> The refresh interval is determined solely by the **file name**: `ark_usage.4s.py` → 4 seconds. To reduce resource usage, rename it to `ark_usage.10s.py` or `ark_usage.1m.py` (and set `ROTATE_SECONDS` in the script to the same number — see [Menu Bar Rotation Strategy](#menu-bar-rotation-strategy)).

---

## Configuring the Cookie

The plugin pulls usage from Volcengine's internal console API, so it needs the cookie of a **logged-in console session**. You only need to do this once, then refresh the cookie whenever it expires.

1. Sign in to the [Volcengine Ark console](https://console.volcengine.com/ark) and open the subscription page.
2. Open DevTools `F12` → `Network`, reload the page, and find the `GetCodingPlanUsage` request.
3. Right-click it → **Copy → Copy as cURL**.
4. Click the menu bar icon → **「从剪贴板更新 Cookie」** (Update Cookie from Clipboard).

The script parses `Cookie` and `x-web-id` from the clipboard and writes them to `~/.config/ark_config.json` — no manual editing required.

> Besides "Copy as cURL", the parser also accepts `-b '...'` / `--cookie '...'` / `Cookie: ...` forms.

**Cookie age indicators**

- **≥ 7 days** since last update: `⚠️` appears in the menu bar
- **≥ 14 days** since last update: `🔴` appears in the menu bar
- API returns 401/403: the menu bar shows "Cookie 过期" (cookie expired)

---

## Menu Bar Rotation Strategy

This is the core behavior of the fork, so it deserves its own section.

**1. How often it runs = the interval in the file name**

SwiftBar runs the script every **4 seconds** based on the file name `ark_usage.4s.py`.

**2. The rotation period deliberately matches the refresh interval**

`ROTATE_SECONDS = 4` inside the script equals the refresh interval. That way **every refresh advances exactly one level** — no wasted refreshes rendering the same content, and no level is skipped.

**3. Stateless, clock-driven rotation**

Which level is currently shown is decided entirely by the **system clock**, with no local state file:

```python
idx = int(time.time() // ROTATE_SECONDS) % len(records)
```

Consequences:

- Running the script repeatedly never accumulates state; restarting the Mac or SwiftBar resumes at the correct level immediately;
- Multiple instances (or manual runs) always agree on the level;
- One full cycle = 3 levels × 4 s = **12 seconds**, in the order **session → weekly → monthly**.

**4. Constant title width**

The title is `short label + space + percentage + optional flags`, e.g. `时 23%`, `月 26%`.

To keep the menu bar icon from **jittering as the number changes** (which would shove other menu bar icons around), two things are done:

- Use the **monospaced** font `Menlo` (`font=Menlo`). macOS's default SF Pro uses proportional digits — `1` is narrower than `0`, so padding with spaces does not align; the same title varies by ~8pt across values in practice;
- Right-align the percentage to a **fixed width of 2 characters** and **cap the displayed value at 99** (`PCT_DIGITS = 2`, `PCT_MAX = 99`). Every title is then exactly 5 characters and its width is identical (measured: 44.3pt at Menlo 12). A 3-digit value would widen it, so the menu bar caps at `99%` while the dropdown still shows the true value (e.g. `100.0%`).

---

## Performance Impact

The more responsive the rotation, the more often the script runs. That is the main reason this fork adds caching.

**Hot / cold code paths**

| Path | Trigger | Behavior |
| --- | --- | --- |
| Hot | Cache still valid | Reads the plain-text cache and prints only; **does not import `json` / `pathlib`**; no network request |
| Cold | Cache expired / retry cooldown finished / config file changed | Imports `json` and `urllib`, calls the API, writes the cache back |

Relevant constants:

- `API_CACHE_TTL = 60`: the API result is cached for 60 seconds, i.e. **the API is hit at most once every 60 seconds** (that window covers 15 refreshes, only 1 of which calls the API).
- `API_RETRY_COOLDOWN = 30`: after a failure, no retry for 30 seconds, so a network outage does not cause a request storm.

**Measured numbers**

Method: `resource.getrusage(RUSAGE_CHILDREN)`, on Python 3.13.13 (miniforge), Apple Silicon macOS.

| Scenario | CPU per run | Wall time per run |
| --- | --- | --- |
| Hot path (cache read, average of 20) | **≈ 18 ms** | ≈ 20 ms |
| Cold path (includes HTTP request) | **≈ 409 ms** | ≈ 631 ms |

**Estimated daily cost at a 4-second refresh**

- Runs per day: `86400 / 4 = 21,600`
- Of which cold: `86400 / 60 = 1,440`; hot: `20,160`
- Daily CPU ≈ `1,440 × 409 ms + 20,160 × 18 ms ≈ 588 + 365 ≈ 952 CPU·s/day`
- That is roughly **1.1% of a single core** (continuous, but negligible on a multi-core machine)
- API requests per day: **at most 1,440**

**Versus upstream (5-minute refresh, no caching)**

| | This fork (4s rotation + cache) | Upstream (5m, no cache) |
| --- | --- | --- |
| Runs per day | 21,600 | 288 |
| API requests per day | ≤ 1,440 | 288 (every run) |
| Daily CPU | ≈ 952 CPU·s (~1.1% of a core) | ≈ 118 CPU·s (~0.14% of a core) |

Runs increase 75×, but **API requests only increase 5×** — that is the payoff of the 60-second cache.

**How to lower the cost**

1. Rename the file to a longer interval (e.g. `ark_usage.10s.py`) and set `ROTATE_SECONDS` to the same value;
2. Increase `API_CACHE_TTL` (e.g. to 300 s) to cut API requests proportionally;
3. Drop rotation entirely and show a single level (simplify the handling and pin one level in the menu bar).

> Note: the cold-path figure comes from measuring the same request logic (`fetch_quota`); both versions make an identical API call.

---

## Dropdown Menu

- Progress bars for all three levels, with a `▶` marker on the level currently shown in the menu bar;
- **All three reset countdowns** are always shown, independent of the menu bar rotation;
- 「切换为圆点/方块进度条」— switch the bar style; written to the config file and applied immediately;
- 「从剪贴板更新 Cookie」— see [Configuring the Cookie](#configuring-the-cookie);
- 「打开火山控制台」— open the Volcengine Ark console.

---

## Configuration

Edit the constants at the top of the script:

| Constant | Default | Description |
| --- | --- | --- |
| `ROTATE_SECONDS` | `4` | Menu bar rotation period (seconds); must match the interval in the file name |
| `TITLE_FONT` | `Menlo` | Menu bar font; must be monospaced to keep the title width constant |
| `TITLE_SIZE` | `12` | Menu bar font size |
| `PCT_DIGITS` | `2` | Digit width for the percentage (right-aligned) |
| `PCT_MAX` | `99` | Menu bar display cap, to avoid a 3rd digit widening the title |
| `API_CACHE_TTL` | `60` | API result cache lifetime (seconds) |
| `API_RETRY_COOLDOWN` | `30` | Retry cooldown after a failure (seconds) |

**File locations**

| File | Path | Purpose |
| --- | --- | --- |
| Config | `~/.config/ark_config.json` | Cookie, `x-web-id`, bar style |
| Cache | `~/.config/ark_usage_cache.txt` | Plain-text usage cache and timestamps |

The cache is plain-text `key=value` so the hot path can parse it without importing `json`:

```
written_at=1755000000.000
config_mtime=1754000000.000
bar_style=dots
error=
session=38.5000|1755003600
```

---

## Differences from upstream

This fork keeps all upstream features; the main differences:

| Aspect | Upstream `ark_usage.5m.py` | This fork `ark_usage.4s.py` |
| --- | --- | --- |
| Menu bar content | Only the **highest** of the three levels | **Rotates** through all three (session → weekly → monthly) |
| Data fetch | Calls the API on every refresh | Hot/cold paths + 60-second cache |
| Menu bar font | System default (width varies with the number) | Monospaced `Menlo` + fixed 2 digits, constant width |
| Bar style | One fixed style | Dot / block, switchable (persisted) |
| Cookie update | — | One-click parse from clipboard |
| Refresh interval | 5 minutes | 4 seconds |

---

## FAQ

| Issue | Fix |
| --- | --- |
| Menu bar shows "未配置 Cookie" | Complete [Configuring the Cookie](#configuring-the-cookie) first |
| Menu bar shows "Cookie 过期" | Cookie invalid (API returns 401/403); copy a fresh cURL and update |
| `⚠️` / `🔴` in the menu bar | Cookie is 7 / 14 days old; update it soon |
| `⚠️` after the number | The last request failed; cached data from the previous fetch is being shown |
| Icon still changes width | Make sure `TITLE_FONT = "Menlo"` is unchanged; a non-monospaced font brings back the jitter |
| Want to cut CPU usage | See [How to lower the cost](#performance-impact) |
| Plugin doesn't refresh | Check the file is executable, the shebang points to a valid Python, and the file name contains an interval (e.g. `.4s.`) |

---

## License

MIT, same as the original repository.
