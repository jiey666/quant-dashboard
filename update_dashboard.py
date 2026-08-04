#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
全量数据自动更新脚本 — 在 GitHub Actions 云端运行
获取所有仪表盘所需数据并注入到 dashboard.html

数据来源:
  - 新浪财经API: 5/30/60分钟K线 → 真实分钟级KDJ(9,3,3)
  - 腾讯日K线API: 日K线200根 → MACD/MA/RSI/趋势/30日高开率/20日跌幅
  - 腾讯实时行情API: PE/PB/量比/主力资金
  - 东方财富板块API: 板块行情 → 板块共振

输出: dashboard.html (更新KDJ_FALLBACK + SECTOR_FALLBACK)
"""
import json, time, urllib.request, ssl, re, sys, os, datetime

# === 配置 ===
DASHBOARD_FILE = os.environ.get('DASHBOARD_FILE', 'dashboard.html')
GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN', '')
REPO = os.environ.get('GITHUB_REPOSITORY', 'jiey666/quant-dashboard')

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36',
    'Referer': 'https://finance.sina.com.cn/'
}

def fetch_url(url, timeout=15, headers=None):
    h = headers or HEADERS
    try:
        req = urllib.request.Request(url, headers=h)
        resp = urllib.request.urlopen(req, timeout=timeout, context=ctx)
        return resp.read()
    except Exception as e:
        return None

def fetch_text(url, timeout=15, headers=None, encoding='utf-8'):
    raw = fetch_url(url, timeout, headers)
    if raw is None:
        return None
    try:
        return raw.decode(encoding, errors='replace')
    except:
        return raw.decode('latin-1', errors='replace')

# === 股票代码列表 ===
def get_stock_codes(html):
    codes = []
    for m in re.finditer(r'"code":\s*"(s[hz]\d{6})"', html):
        code = m.group(1)
        if code not in codes:
            codes.append(code)
    return codes

def get_stock_sectors(html):
    sectors = {}
    for m in re.finditer(r'"code":\s*"(s[hz]\d{6})"[^}]*?"sector":\s*"([^"]+)"', html):
        sectors[m.group(1)] = m.group(2)
    return sectors

# === 新浪分钟K线 API ===
def fetch_sina_kline(code, scale, datalen=200):
    url = (f'https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/'
           f'CN_MarketData.getKLineData?symbol={code}&scale={scale}&ma=no&datalen={datalen}')
    text = fetch_text(url, timeout=12, headers={
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Referer': 'https://finance.sina.com.cn/'
    })
    if not text or text.strip() == '[]':
        return None
    try:
        data = json.loads(text)
        if not data or not isinstance(data, list):
            return None
        result = []
        for item in data:
            if 'close' in item and 'high' in item and 'low' in item:
                result.append({
                    'open': float(item['open']),
                    'close': float(item['close']),
                    'high': float(item['high']),
                    'low': float(item['low']),
                })
        return result if len(result) >= 10 else None
    except:
        return None

# === 腾讯日K线 API ===
def fetch_daily_kline(code, lmt=200):
    url = f'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={code},day,,,{lmt},qfq'
    text = fetch_text(url, timeout=12, headers={
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Referer': 'https://gu.qq.com/'
    })
    if not text:
        return None
    try:
        data = json.loads(text)
        if not data or data.get('code') != 0:
            return None
        stock_data = data.get('data', {}).get(code, {})
        raw = stock_data.get('qfqday') or stock_data.get('day')
        if not raw:
            return None
        result = []
        for item in raw:
            if len(item) >= 5:
                result.append({
                    'date': item[0],
                    'open': float(item[1]),
                    'close': float(item[2]),
                    'high': float(item[3]),
                    'low': float(item[4]),
                })
        return result
    except:
        return None

# === 腾讯实时行情 API ===
def fetch_realtime_quote(code):
    url = f'https://qt.gtimg.cn/q={code}'
    raw = fetch_url(url, timeout=8, headers={
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Referer': 'https://gu.qq.com/'
    })
    if not raw:
        return None
    try:
        text = raw.decode('gbk', errors='replace')
    except:
        text = raw.decode('latin-1', errors='replace')
    match = re.search(r'v_' + code + r'="([^"]*)"', text)
    if not match:
        return None
    fields = match.group(1).split('~')
    if len(fields) < 50:
        return None
    result = {}
    try:
        if len(fields) > 1 and fields[1]:
            result['name'] = fields[1]
        if len(fields) > 3 and fields[3]:
            result['price'] = float(fields[3])
        if len(fields) > 32 and fields[32]:
            result['pe'] = float(fields[32])
        if len(fields) > 46 and fields[46]:
            result['pb'] = float(fields[46])
        # 量比
        if len(fields) > 49 and fields[49]:
            result['volRatio'] = float(fields[49])
        # 涨跌幅
        if len(fields) > 32 and fields[32]:
            pass
        if len(fields) > 3 and fields[3] and len(fields) > 4 and fields[4]:
            try:
                cur = float(fields[3])
                prev = float(fields[4])
                if prev > 0:
                    result['changePct'] = round((cur - prev) / prev * 100, 2)
            except:
                pass
        # 换手率
        if len(fields) > 38 and fields[38]:
            result['turnover'] = float(fields[38])
        # 主力净流入(万元)
        if len(fields) > 78 and fields[78]:
            try:
                result['mainFlow'] = float(fields[78])
            except:
                pass
        # 5日主力净流入
        if len(fields) > 79 and fields[79]:
            try:
                result['mainFlow5d'] = float(fields[79])
            except:
                pass
    except:
        pass
    return result

# === 东方财富板块 API ===
def fetch_hot_sectors():
    sectors = []
    for domain in ['push2.eastmoney.com', 'push2delay.eastmoney.com']:
        url = (f'https://{domain}/api/qt/clist/get?pn=1&pz=60&po=1&np=1&'
               f'fltt=2&invt=2&fs=m:90+t:2&fields=f12,f14,f3,f62,f184,f66,f69,f72,f75,f136,f152')
        text = fetch_text(url, timeout=10, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'https://quote.eastmoney.com/'
        })
        if not text:
            continue
        try:
            data = json.loads(text)
            if data and data.get('data') and data['data'].get('diff'):
                for s in data['data']['diff']:
                    sectors.append({
                        'code': str(s.get('f12', '')),
                        'name': s.get('f14', ''),
                        'changePct': round(s.get('f3', 0), 2),
                        'mainFlow': round(s.get('f62', 0) / 1e8, 2),
                        'mainFlowPct': round(s.get('f184', 0), 2),
                        'superLargeFlow': round(s.get('f66', 0) / 1e8, 2),
                        'largeFlow': round(s.get('f72', 0) / 1e8, 2),
                        'upCount': s.get('f136', 0),
                        'downCount': s.get('f152', 0),
                    })
                break
        except:
            continue
    return sectors

# === 指标计算 ===
def calc_kdj(klines, n=9):
    if not klines or len(klines) < n:
        return None
    k = 50.0
    d = 50.0
    k_prev = 50.0
    d_prev = 50.0
    for i in range(len(klines)):
        start = max(0, i - n + 1)
        period = klines[start:i+1]
        hh = max(c['high'] for c in period)
        ll = min(c['low'] for c in period)
        close = klines[i]['close']
        rsv = 50.0 if hh == ll else (close - ll) / (hh - ll) * 100.0
        k = (2.0/3.0) * k_prev + (1.0/3.0) * rsv
        d = (2.0/3.0) * d_prev + (1.0/3.0) * k
        k_prev = k
        d_prev = d
    j = 3 * k - 2 * d
    return {'k': round(k, 1), 'd': round(d, 1), 'j': round(j, 1)}

def calc_kdj_full(klines, n=9):
    if not klines or len(klines) < n + 1:
        return None
    now = calc_kdj(klines, n)
    if not now:
        return None
    prev = calc_kdj(klines[:-1], n)
    if not prev:
        cross = '多头' if now['k'] > now['d'] else '空头'
    elif prev['k'] <= prev['d'] and now['k'] > now['d']:
        cross = '金叉'
    elif prev['k'] >= prev['d'] and now['k'] < now['d']:
        cross = '死叉'
    elif now['k'] > now['d']:
        cross = '多头'
    else:
        cross = '空头'
    return {'k': now['k'], 'd': now['d'], 'j': now['j'], 'cross': cross}

def calc_macd(klines, short=12, long=26, mid=9):
    if len(klines) < long + mid:
        return None
    closes = [k['close'] for k in klines]
    def ema(data, n):
        result = [data[0]]
        a = 2 / (n + 1)
        for v in data[1:]:
            result.append(a * v + (1 - a) * result[-1])
        return result
    ema_short = ema(closes, short)
    ema_long = ema(closes, long)
    dif = [s - l for s, l in zip(ema_short, ema_long)]
    dea = ema(dif, mid)
    macd_val = [(d - e) * 2 for d, e in zip(dif, dea)]
    return {'dif': round(dif[-1], 3), 'dea': round(dea[-1], 3), 'macd': round(macd_val[-1], 3)}

def calc_rsi(klines, n=14):
    if len(klines) < n + 1:
        return None
    gains = []
    losses = []
    for i in range(1, len(klines)):
        diff = klines[i]['close'] - klines[i-1]['close']
        gains.append(max(0, diff))
        losses.append(max(0, -diff))
    avg_gain = sum(gains[-n:]) / n
    avg_loss = sum(losses[-n:]) / n
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - 100 / (1 + rs), 2)

def calc_ma(klines):
    if len(klines) < 20:
        return None
    closes = [k['close'] for k in klines]
    ma5 = sum(closes[-5:]) / 5
    ma10 = sum(closes[-10:]) / 10
    ma20 = sum(closes[-20:]) / 20
    return {
        'ma5': round(ma5, 2), 'ma10': round(ma10, 2), 'ma20': round(ma20, 2),
        'aligned': ma5 > ma10 > ma20, 'aboveMa20': closes[-1] > ma20,
    }

def calc_trend(klines):
    if not klines or len(klines) < 6:
        return None
    closes = [k['close'] for k in klines]
    cur = closes[-1]
    ago5 = closes[-6]
    trendPct = round((cur - ago5) / ago5 * 100, 1)
    def ma(n):
        if len(closes) < n: return 0
        return sum(closes[-n:]) / n
    ma5, ma10, ma20 = ma(5), ma(10), ma(20)
    trend = '震荡整理'
    if cur > ma5 and ma5 > ma10 and ma10 > ma20:
        trend = '上涨趋势'
    elif cur < ma5 and ma5 < ma10 and ma10 < ma20:
        trend = '下跌趋势'
    return {'trend': trend, 'trendPct': trendPct}

def calc_open_rate_30d(klines):
    if len(klines) < 31:
        return 0
    recent = klines[-31:]
    count = sum(1 for i in range(1, len(recent)) if recent[i]['open'] > recent[i-1]['close'])
    return round(count / 30 * 100, 1)

def calc_drop_20d(klines):
    if len(klines) < 21:
        return 0
    closes = [k['close'] for k in klines]
    return round((closes[-1] - closes[-21]) / closes[-21] * 100, 2)

def format_flow(val):
    if val is None or val == 0:
        return None
    abs_v = abs(val)
    if abs_v >= 10000:
        return f"{val/10000:+.2f}亿"
    return f"{val:+.0f}万"

# === 板块共振匹配 ===
def match_sector(stock_sector, sectors_sorted):
    if not stock_sector:
        return None
    sector_name = stock_sector.replace('热门', '').strip()
    if not sector_name or sector_name == '标的':
        return None
    for i, hs in enumerate(sectors_sorted):
        hs_name = hs.get('name', '') or ''
        if not hs_name:
            continue
        if hs_name in sector_name or sector_name in hs_name:
            chg = hs.get('changePct', 0)
            flow = hs.get('mainFlow', 0)
            if i < 5:
                return ('板块启动', f"{hs_name} 涨{chg:.1f}% 流入{flow:.1f}亿")
            elif i < 15:
                return ('部分共振', f"{hs_name} 涨{chg:.1f}%")
            elif i < 30:
                return ('弱共振', f"{hs_name} 涨{chg:.1f}%")
            else:
                return ('同板块', f"{hs_name} 涨{chg:.1f}%")
    return None

# === 主流程 ===
def main():
    print(f"=== Dashboard Auto Update @ {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===")

    # 1. 读取HTML模板
    html_path = DASHBOARD_FILE
    if not os.path.exists(html_path):
        # 尝试从GitHub API下载
        print(f"Local {html_path} not found, downloading from GitHub...")
        url = f'https://raw.githubusercontent.com/{REPO}/main/dashboard.html'
        raw = fetch_url(url, timeout=30)
        if raw:
            with open(html_path, 'wb') as f:
                f.write(raw)
            print(f"Downloaded {len(raw)} bytes")
        else:
            print("ERROR: Cannot find dashboard.html")
            sys.exit(1)

    with open(html_path, 'r', encoding='utf-8') as f:
        html = f.read()
    print(f"HTML loaded: {len(html)} bytes")

    # 2. 提取股票代码和板块
    codes = get_stock_codes(html)
    stock_sectors = get_stock_sectors(html)
    print(f"Stocks: {len(codes)} | Sectors mapped: {len(stock_sectors)}")

    # 3. 获取板块数据
    print("\n--- Fetching sector data ---")
    sectors = fetch_hot_sectors()
    if not sectors:
        print("WARNING: Sector API failed, keeping existing SECTOR_FALLBACK")
        # 从HTML中提取现有的
        m = re.search(r'var SECTOR_FALLBACK = (\[.*?\]);', html, re.DOTALL)
        if m:
            sectors = json.loads(m.group(1))
            print(f"Using existing {len(sectors)} sectors from HTML")
    else:
        print(f"Got {len(sectors)} sectors")
    sectors_sorted = sorted(sectors, key=lambda s: s.get('mainFlow', 0), reverse=True)

    # 4. 逐股获取数据
    print(f"\n--- Fetching data for {len(codes)} stocks ---")
    fallback = {}
    success = 0

    for i, code in enumerate(codes):
        print(f"[{i+1}/{len(codes)}] {code}...", end=' ', flush=True)
        entry = {}

        # 4a. 分钟KDJ (新浪API)
        for scale, prefix in [(5, 'kd5'), (30, 'kd30'), (60, 'kd60')]:
            klines = fetch_sina_kline(code, scale, datalen=200)
            if klines and len(klines) >= 10:
                kdj = calc_kdj_full(klines, n=9)
                if kdj:
                    entry[f'{prefix}_k'] = kdj['k']
                    entry[f'{prefix}_d'] = kdj['d']
                    entry[f'{prefix}_cross'] = kdj['cross']
            time.sleep(0.05)

        # 4b. 日K线指标 (腾讯API)
        daily = fetch_daily_kline(code, lmt=200)
        if daily and len(daily) >= 30:
            # MACD
            macd = calc_macd(daily)
            if macd:
                dif, dea, hist = macd['dif'], macd['dea'], macd['macd']
                cross = '金叉' if hist > 0 and dif > dea else ('死叉' if hist < 0 and dif < dea else ('红柱' if hist > 0 else '绿柱'))
                entry['macd'] = f"DIF:{dif:+.2f} DEA:{dea:+.2f} {cross}"

            # MA
            ma = calc_ma(daily)
            if ma:
                entry['ma'] = '多头排列' if ma['aligned'] else '交叉震荡'
                entry['ma5'] = ma['ma5']
                entry['ma10'] = ma['ma10']
                entry['ma20'] = ma['ma20']
                entry['maAbove'] = ma['aboveMa20']

            # RSI
            rsi = calc_rsi(daily)
            if rsi:
                entry['rsi'] = rsi

            # 趋势
            tr = calc_trend(daily)
            if tr:
                entry['trend'] = tr['trend']
                entry['trendPct'] = tr['trendPct']

            # 30日高开率
            or30 = calc_open_rate_30d(daily)
            entry['openRate30d'] = or30
            entry['openRateTotal'] = or30
            entry['openRateScore'] = 5 if or30 >= 70 else (4 if or30 >= 60 else (3 if or30 >= 50 else (2 if or30 >= 40 else 1)))

            # 20日跌幅
            entry['drop20d'] = calc_drop_20d(daily)

        # 4c. 实时行情 (腾讯API)
        quote = fetch_realtime_quote(code)
        if quote:
            if 'pe' in quote and quote['pe'] > 0:
                entry['pe'] = quote['pe']
            if 'pb' in quote and quote['pb'] > 0:
                entry['pb'] = quote['pb']
            if 'volRatio' in quote:
                entry['volRatio'] = quote['volRatio']
            if 'mainFlow' in quote and quote['mainFlow'] != 0:
                flow_str = format_flow(quote['mainFlow'])
                if flow_str:
                    entry['mainFlow'] = flow_str
                    entry['mainFlowDate'] = '今日'
            if 'mainFlow5d' in quote and quote['mainFlow5d'] != 0:
                flow_str = format_flow(quote['mainFlow5d'])
                if flow_str:
                    entry['mainFlow5d'] = flow_str

        # 4d. 板块共振
        sector = stock_sectors.get(code)
        if sector and sectors_sorted:
            res = match_sector(sector, sectors_sorted)
            if res:
                entry['sectorResonance'] = res[0]
                entry['sectorResonanceDetail'] = res[1]

        if entry:
            fallback[code] = entry
            success += 1
            kd5 = entry.get('kd5_k', '--')
            kd30 = entry.get('kd30_k', '--')
            kd60 = entry.get('kd60_k', '--')
            pe = entry.get('pe', '--')
            print(f"5m:{kd5} 30m:{kd30} 60m:{kd60} PE:{pe}")
        else:
            print("FAIL")

        time.sleep(0.15)

    print(f"\n=== Success: {success}/{len(codes)} ===")

    # 5. 注入到HTML
    update_time = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    # 替换 KDJ_FALLBACK
    fb_json = json.dumps(fallback, ensure_ascii=False, separators=(',', ':'))
    new_fallback_block = f"// PRE-COMPUTED FALLBACK (auto-updated: {update_time})\nvar KDJ_FALLBACK = {fb_json};"

    pattern = re.compile(r'// PRE-COMPUTED FALLBACK.*?var KDJ_FALLBACK = \{.*?\};', re.DOTALL)
    if pattern.search(html):
        html = pattern.sub(new_fallback_block, html, count=1)
        print("Replaced KDJ_FALLBACK block")
    else:
        # 直接替换 var KDJ_FALLBACK = {...};
        html = re.sub(r'var KDJ_FALLBACK\s*=\s*\{.*?\};', new_fallback_block, html, count=1, flags=re.DOTALL)
        print("Replaced KDJ_FALLBACK (direct)")

    # 替换 SECTOR_FALLBACK
    if sectors:
        sec_json = json.dumps(sectors, ensure_ascii=False, separators=(',', ':'))
        new_sec_block = f"var SECTOR_FALLBACK = {sec_json};"
        html = re.sub(r'var SECTOR_FALLBACK\s*=\s*\[.*?\];', new_sec_block, html, count=1, flags=re.DOTALL)
        print(f"Replaced SECTOR_FALLBACK ({len(sectors)} sectors)")

    # 6. 保存
    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"\nSaved to {html_path} ({len(html)} bytes)")
    print(f"Update time: {update_time}")

    # 7. 如果有GitHub Token, 通过API推送
    if GITHUB_TOKEN:
        print("\n--- Pushing to GitHub via API ---")
        push_to_github(html, update_time)

def push_to_github(html_content, update_time):
    """通过GitHub Content API推送更新"""
    import base64
    api_url = f'https://api.github.com/repos/{REPO}/contents/dashboard.html'

    # 获取当前文件的sha (用于更新)
    req = urllib.request.Request(api_url, headers={
        'Authorization': f'token {GITHUB_TOKEN}',
        'Accept': 'application/vnd.github+json',
    })
    try:
        resp = urllib.request.urlopen(req, timeout=15, context=ctx)
        file_info = json.loads(resp.read())
        sha = file_info.get('sha')
        print(f"Current file sha: {sha}")
    except Exception as e:
        print(f"Warning: cannot get file sha: {e}")
        sha = None

    # 上传
    content_b64 = base64.b64encode(html_content.encode('utf-8')).decode('ascii')
    data = json.dumps({
        'message': f'Auto-update dashboard data @ {update_time}',
        'content': content_b64,
        'sha': sha,
        'branch': 'main'
    }).encode('utf-8')

    req = urllib.request.Request(api_url, data=data, headers={
        'Authorization': f'token {GITHUB_TOKEN}',
        'Accept': 'application/vnd.github+json',
        'Content-Type': 'application/json',
    }, method='PUT')

    try:
        resp = urllib.request.urlopen(req, timeout=30, context=ctx)
        result = json.loads(resp.read())
        print(f"Push successful! Commit: {result.get('commit', {}).get('sha', 'unknown')[:8]}")
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"Push failed: {e.code} {e.reason}")
        print(f"Response: {body[:300]}")
    except Exception as e:
        print(f"Push error: {e}")

if __name__ == '__main__':
    main()
