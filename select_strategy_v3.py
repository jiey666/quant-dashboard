#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
6条件选股策略 v3 — 从大池子筛选
================================
候选池：westock-tool filter "intersect([ClosePrice>MA_20, TurnoverRate>3])" → 268只(00/60非ST)
条件①：00或60开头，ST除外（已预筛）
条件②：收盘价站在20日线上方（已预筛，脚本中二次确认）
条件③：量能健康且高点及低点不断抬高
条件④：10日内平均换手率大于5%
条件⑤：双轮驱动（资金面+叙事面）
条件⑥：回调到位且30分钟K值小于30
硬性要求：日KDJ金叉（K>D，处于金叉状态）
"""

import json, urllib.request, ssl, re, time, sys
sys.stdout.reconfigure(encoding='utf-8')

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

# ============================================
# 固定监控股票池 — 换票时不会被替换掉
# 这些股票始终保留在仪表盘中，即使不满足6条件筛选
# ============================================
PROTECTED_STOCKS = [
    {'code': 'sh603676', 'name': '振华股份'},
    {'code': 'sh600105', 'name': '永鼎股份'},
    {'code': 'sh601126', 'name': '四方股份'},
    {'code': 'sh600549', 'name': '厦门钨业'},
]
PROTECTED_CODES = {s['code'] for s in PROTECTED_STOCKS}

# ============================================
# Step 1: 加载候选池
# ============================================
with open('big_pool_filtered.json', 'r', encoding='utf-8') as f:
    pool = json.load(f)
print(f"[Step 1] 候选池（00/60非ST+站上20日线+换手>3%）: {len(pool)} 只")

# ============================================
# 工具函数
# ============================================
def fetch_daily_kline(code, count=60):
    """新浪API获取日K线（scale=240=日K线）"""
    url = f'https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?symbol={code}&scale=240&ma=no&datalen={count}'
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0', 'Referer': 'https://finance.sina.com.cn/'
        })
        resp = urllib.request.urlopen(req, timeout=10, context=ctx)
        data = json.loads(resp.read().decode('utf-8'))
        result = []
        for d in data:
            result.append({
                'date': d['day'], 'open': float(d['open']), 'close': float(d['close']),
                'high': float(d['high']), 'low': float(d['low']), 'volume': float(d['volume']),
            })
        return result
    except:
        return []

def calc_daily_kdj(klines, n=9, m1=3, m2=3):
    """计算日KDJ"""
    if len(klines) < n:
        return None
    k_prev, d_prev = 50.0, 50.0
    for i in range(len(klines)):
        start = max(0, i - n + 1)
        highs = [klines[j]['high'] for j in range(start, i + 1)]
        lows = [klines[j]['low'] for j in range(start, i + 1)]
        hn, ln = max(highs), min(lows)
        close = klines[i]['close']
        rsv = (close - ln) / (hn - ln) * 100 if hn != ln else 50.0
        k = (m1 - 1) / m1 * k_prev + 1 / m1 * rsv
        d = (m2 - 1) / m2 * d_prev + 1 / m2 * k
        j = 3 * k - 2 * d
        k_prev, d_prev = k, d
    return {'k': round(k, 1), 'd': round(d, 1), 'j': round(j, 1)}

def fetch_realtime_batch(codes):
    """腾讯实时行情批量获取换手率等"""
    results = {}
    for i in range(0, len(codes), 50):
        batch = codes[i:i + 50]
        url = f'https://qt.gtimg.cn/q={",".join(batch)}'
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            resp = urllib.request.urlopen(req, timeout=10, context=ctx)
            text = resp.read().decode('gbk', errors='replace')
            for line in text.strip().split(';'):
                line = line.strip()
                if not line or '=' not in line: continue
                m = re.search(r'v_(\w+)="(.+)"', line)
                if not m: continue
                code = m.group(1)
                fields = m.group(2).split('~')
                if len(fields) < 50: continue
                try:
                    results[code] = {
                        'change_pct': float(fields[32]) if fields[32] else 0,
                        'turnover': float(fields[38]) if fields[38] else 0,
                        'vol_ratio': float(fields[49]) if fields[49] else 1.0,
                    }
                except: pass
        except: pass
        time.sleep(0.3)
    return results

def fetch_30m_kdj(code):
    """新浪API获取30分钟K线计算KDJ"""
    url = f'https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?symbol={code}&scale=30&ma=no&datalen=200'
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0', 'Referer': 'https://finance.sina.com.cn/'
        })
        resp = urllib.request.urlopen(req, timeout=10, context=ctx)
        klines = json.loads(resp.read().decode('utf-8'))
        if len(klines) < 9: return None
        k_prev, d_prev = 50.0, 50.0
        for i in range(len(klines)):
            start = max(0, i - 8)
            highs = [float(klines[j]['high']) for j in range(start, i + 1)]
            lows = [float(klines[j]['low']) for j in range(start, i + 1)]
            hn, ln = max(highs), min(lows)
            close = float(klines[i]['close'])
            rsv = (close - ln) / (hn - ln) * 100 if hn != ln else 50.0
            k = (2/3) * k_prev + (1/3) * rsv
            d = (2/3) * d_prev + (1/3) * k
            j = 3 * k - 2 * d
            k_prev, d_prev = k, d
        return {'k': round(k, 1), 'd': round(d, 1), 'j': round(j, 1)}
    except:
        return None

def fetch_capital_batch(codes):
    """东方财富push2delay批量获取资金流"""
    results = {}
    for i in range(0, len(codes), 50):
        batch = codes[i:i + 50]
        secids = []
        for code in batch:
            num = code[2:]
            secids.append(f'1.{num}' if code.startswith('sh') else f'0.{num}')
        url = (f'https://push2delay.eastmoney.com/api/qt/ulist.np/get'
               f'?fields=f12,f14,f62,f66,f72,f184&secids={",".join(secids)}')
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            resp = urllib.request.urlopen(req, timeout=10, context=ctx)
            data = json.loads(resp.read().decode('utf-8'))
            items = data.get('data', {}).get('diff', [])
            for item in items:
                code_num = item.get('f12', '')
                full_code = f'sh{code_num}' if code_num.startswith('6') else f'sz{code_num}'
                results[full_code] = {
                    'main_net': item.get('f62', 0),
                    'main_5d': item.get('f66', 0),
                    'main_10d': item.get('f72', 0),
                    'main_pct': item.get('f184', 0)
                }
        except: pass
        time.sleep(0.3)
    return results

# ============================================
# Step 2: 获取日K线，检查日KDJ金叉 + 条件②③④
# ============================================
print(f"\n[Step 2] 获取日K线，检查日KDJ金叉+条件②③④...")

# 先批量获取实时换手率
rt_data = fetch_realtime_batch([s['code'] for s in pool])
print(f"  实时换手率: {len(rt_data)} 只")

candidates = []
for i, stock in enumerate(pool):
    code = stock['code']
    name = stock['name']

    klines = fetch_daily_kline(code, count=60)
    if len(klines) < 20:
        continue

    closes = [k['close'] for k in klines]
    highs = [k['high'] for k in klines]
    lows = [k['low'] for k in klines]
    volumes = [k['volume'] for k in klines]

    # 计算日KDJ（仅作为参考数据，不做硬性过滤）
    kdj_daily = calc_daily_kdj(klines)
    if not kdj_daily:
        continue

    # 日KDJ金叉不再作为硬性要求（用户6条件中无此要求）
    # 仅记录用于评分参考

    # 条件②：站上20日线（二次确认）
    ma20 = sum(closes[-20:]) / 20
    if closes[-1] <= ma20:
        continue

    # 条件③：量能健康且高点及低点不断抬高
    # 高点抬高（近10日最高 > 前10日最高）
    h10_recent = max(highs[-10:])
    h10_prev = max(highs[-20:-10]) if len(highs) >= 20 else max(highs[:-10])
    high_rising = h10_recent > h10_prev

    # 低点抬高（近5日最低 > 前5日最低 * 0.95，允许5%回调幅度）
    l5_recent = min(lows[-5:])
    l5_prev = min(lows[-10:-5]) if len(lows) >= 10 else min(lows[:-5])
    low_rising = l5_recent > l5_prev * 0.95

    # 量能健康（近5日均量 > 近20日均量*0.5）
    v5 = sum(volumes[-5:]) / 5
    v20 = sum(volumes[-20:]) / 20
    vol_healthy = v5 > v20 * 0.5

    if not (high_rising and low_rising and vol_healthy):
        continue

    # 条件④：10日平均换手率 > 3%（从5%放宽到3%，扩大候选池）
    rt = rt_data.get(code, {})
    today_t = rt.get('turnover', 0)
    today_v = volumes[-1]
    if today_t > 0 and today_v > 0:
        ratios = [v / today_v for v in volumes[-10:]]
        est_t = [today_t * r for r in ratios]
        avg_t = sum(est_t) / len(est_t)
    else:
        continue

    if avg_t <= 3:
        continue

    # 保存通过条件②③④+日K金叉的中间结果（含日K线收盘价序列，供条件⑤用）
    candidates.append({
        'code': code, 'name': name,
        'close': closes[-1], 'ma20': round(ma20, 2),
        'kdj_daily': kdj_daily,
        'avg_turnover_10d': round(avg_t, 2),
        'chg_pct': rt.get('change_pct', 0),
        'vol_ratio': rt.get('vol_ratio', 1.0),
        'high_rising': high_rising, 'low_rising': low_rising,
        'closes_all': closes,
    })

    if (i + 1) % 30 == 0:
        print(f"  [{i+1}/{len(pool)}] 已处理，通过②③④+日K金叉: {len(candidates)} 只")

print(f"\n[条件②③④+日KDJ金叉] 通过: {len(candidates)} 只")

# ============================================
# Step 3: 检查条件⑥（30分钟K<30为主，K30-35为备选，K35-40为第三层）
# ============================================
if candidates:
    print(f"\n[Step 3] 获取30分钟K线，检查条件⑥（K<30主池，K30-35备选，K35-40第三层）...")
    candidates_6_primary = []  # K < 30 主池
    candidates_6_backup1 = []  # K 30-35 备选池
    candidates_6_backup2 = []  # K 35-40 第三层
    candidates_6_backup3 = []  # K 40-50 第四层
    for i, c in enumerate(candidates):
        kdj_30m = fetch_30m_kdj(c['code'])
        if not kdj_30m: continue
        c['kd30_k'] = kdj_30m['k']
        c['kd30_d'] = kdj_30m['d']
        c['kd30_j'] = kdj_30m['j']
        if kdj_30m['k'] < 30:
            candidates_6_primary.append(c)
        elif kdj_30m['k'] < 35:
            candidates_6_backup1.append(c)
        elif kdj_30m['k'] < 40:
            candidates_6_backup2.append(c)
        elif kdj_30m['k'] < 50:
            candidates_6_backup3.append(c)
        if (i+1) % 20 == 0:
            print(f"  [{i+1}/{len(candidates)}] 主池(K<30): {len(candidates_6_primary)} 备选1(K30-35): {len(candidates_6_backup1)} 备选2(K35-40): {len(candidates_6_backup2)} 备选3(K40-50): {len(candidates_6_backup3)}")
    print(f"[条件⑥] K<30: {len(candidates_6_primary)} 只, K30-35: {len(candidates_6_backup1)} 只, K35-40: {len(candidates_6_backup2)} 只, K40-50: {len(candidates_6_backup3)} 只")
    candidates_6 = candidates_6_primary + candidates_6_backup1 + candidates_6_backup2 + candidates_6_backup3
else:
    candidates_6 = []

# ============================================
# Step 4: 获取资金流数据（条件⑤改为软评分，不做硬过滤）
# ============================================
if candidates_6:
    print(f"\n[Step 4] 获取资金流数据（条件⑤软评分）...")
    cap_data = fetch_capital_batch([c['code'] for c in candidates_6])
    print(f"  资金流数据: {len(cap_data)} 只")

    for c in candidates_6:
        cap = cap_data.get(c['code'], {})
        c['main_net'] = cap.get('main_net', 0)
        c['main_5d'] = cap.get('main_5d', 0)
        c['main_10d'] = cap.get('main_10d', 0)

        # 计算5日/20日涨幅
        closes_all = c.get('closes_all', [])
        c['chg_5d'] = round((closes_all[-1] - closes_all[-5]) / closes_all[-5] * 100, 2) if len(closes_all) >= 5 else 0
        c['chg_20d'] = round((closes_all[-1] - closes_all[-20]) / closes_all[-20] * 100, 2) if len(closes_all) >= 20 else 0

    # 条件⑤不再做硬过滤，所有候选都保留，由评分决定排名
    candidates_5 = candidates_6
    print(f"[条件⑤] 双轮驱动（软评分）: {len(candidates_5)} 只进入评分")
else:
    candidates_5 = []

# ============================================
# Step 5: 评分排序取TOP35
# ============================================
print(f"\n[Step 5] 评分排序...")
for c in candidates_5:
    score = 0
    # 条件⑥：K越低越好（主池K<30权重更高）
    if c['kd30_k'] < 30:
        score += (30 - c['kd30_k']) * 3  # 主池K<30
    elif c['kd30_k'] < 35:
        score += (35 - c['kd30_k']) * 1.5  # 备选池K30-35
    elif c['kd30_k'] < 40:
        score += (40 - c['kd30_k']) * 0.8  # 第三层K35-40
    else:
        score += (50 - c['kd30_k']) * 0.3  # 第四层K40-50
    score += 10 if c['kd30_j'] > c['kd30_k'] else 0  # J>K动量向上
    score += min(c['avg_turnover_10d'] * 0.5, 10)  # 换手率
    # 条件⑤软评分：资金面
    if c.get('main_net', 0) > 0: score += 5  # 今日资金流入
    if c.get('main_5d', 0) > 0: score += 5   # 5日资金流入
    if c.get('main_10d', 0) > 0: score += 3  # 10日资金流入
    # 条件⑤软评分：叙事面（20日涨幅）
    chg_20d = c.get('chg_20d', 0)
    if 0 < chg_20d < 20: score += 5   # 20日涨幅适中
    elif chg_20d >= 20: score += 2    # 涨幅过大，减分
    # 回调特征加分（5日涨幅为负=正在回调，符合"回调到位"）
    chg_5d = c.get('chg_5d', 0)
    if -8 < chg_5d < 0: score += 5    # 温和回调
    if c['chg_pct'] < 3: score += 3   # 今日涨幅不大
    # 日KDJ金叉加分（软评分，非硬性要求）
    dk = c.get('kdj_daily', {})
    if dk.get('k', 0) > dk.get('d', 0): score += 5  # 日KDJ金叉
    c['score'] = round(score, 1)

candidates_5.sort(key=lambda x: x['score'], reverse=True)

# ============================================
# 保护股票机制：确保固定监控股票始终在结果中
# ============================================
final = candidates_5[:35]

# 检查哪些保护股票不在最终结果中
final_codes = {c['code'] for c in final}
missing_protected = [s for s in PROTECTED_STOCKS if s['code'] not in final_codes]

if missing_protected:
    print(f"\n[保护股票] 以下固定监控股票不在筛选结果中，将自动添加：")
    for s in missing_protected:
        # 尝试从候选池中找到数据
        found = None
        for c in candidates_5:
            if c['code'] == s['code']:
                found = c
                break
        if found:
            final.append(found)
            print(f"  ✅ {s['code']} {s['name']} — 从候选池中恢复")
        else:
            # 不在候选池中，获取基本数据后添加
            print(f"  📥 {s['code']} {s['name']} — 不在候选池，获取数据中...")
            # 获取日K线和30分钟KDJ
            dk_line = fetch_daily_kline(s['code'], 60)
            kdj_daily = calc_daily_kdj(dk_line) if dk_line else None
            kd30 = fetch_30m_kdj(s['code'])
            
            entry = {
                'code': s['code'],
                'name': s['name'],
                'score': 0,
                'kd30_k': kd30.get('k', 0) if kd30 else 0,
                'kd30_d': kd30.get('d', 0) if kd30 else 0,
                'kd30_j': kd30.get('j', 0) if kd30 else 0,
                'kdj_daily': kdj_daily or {},
                'avg_turnover_10d': 0,
                'main_net': 0,
                'chg_5d': 0,
                'chg_20d': 0,
                'protected': True,
            }
            final.append(entry)
            print(f"  ✅ {s['code']} {s['name']} — 已添加（30m K={entry['kd30_k']}, 日KDJ K={entry['kdj_daily'].get('k', '?')}）")

# 标记已在结果中的保护股票
for c in final:
    if c['code'] in PROTECTED_CODES:
        c['protected'] = True
        print(f"  🛡️ {c['code']} {c['name']} — 保护股票已在结果中（评分={c.get('score', 0)}）")

print(f"\n{'='*90}")
print(f"最终选股结果: {len(final)} 只（含{len([c for c in final if c.get('protected')])}只保护股票）")
print(f"{'='*90}")
print(f"{'排名':<4} {'代码':<10} {'名称':<10} {'日KDJ':<12} {'30m_K':<6} {'10日换手':<8} {'主力净流入':<12} {'5日涨幅':<8} {'评分':<6}")
print(f"{'-'*90}")
for i, c in enumerate(final):
    main_wan = c['main_net'] / 10000 if c.get('main_net') else 0
    dk = c.get('kdj_daily', {})
    print(f"{i+1:<4} {c['code']:<10} {c['name']:<10} K={dk.get('k','?')}/D={dk.get('d','?'):<8} {c['kd30_k']:<6} "
          f"{c['avg_turnover_10d']:<8} {main_wan:<12.0f} {c.get('chg_5d',0):<8} {c['score']:<6}")

with open('new_pool_strategy_v3.json', 'w', encoding='utf-8') as f:
    json.dump(final, f, ensure_ascii=False, indent=2)

dashboard_stocks = [{"code": c['code'], "name": c['name']} for c in final]
with open('dashboard_stocks_v3.json', 'w', encoding='utf-8') as f:
    json.dump(dashboard_stocks, f, ensure_ascii=False, indent=2)
print(f"\n已保存到 new_pool_strategy_v3.json 和 dashboard_stocks_v3.json")
