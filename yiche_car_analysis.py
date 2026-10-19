import sys
import io
# 强制 UTF-8 输出（兼容 Windows GBK 终端）
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

import json
import hashlib
import time
import re
import os

import requests
import pandas as pd
import numpy as np
import matplotlib
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.gridspec import GridSpec
from matplotlib import font_manager
from scipy.stats import gaussian_kde
import warnings

warnings.filterwarnings('ignore')

def _setup_font():
    candidates = [
        "C:/Windows/Fonts/simhei.ttf",
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/SimSun.ttc",
    ]
    for fp in candidates:
        if os.path.exists(fp):
            font_manager.fontManager.addfont(fp)
            prop = font_manager.FontProperties(fname=fp)
            plt.rcParams['font.family'] = prop.get_name()
            plt.rcParams['axes.unicode_minus'] = False
            return prop.get_name()
    plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'DejaVu Sans']
    plt.rcParams['axes.unicode_minus'] = False
    return 'SimHei'

FONT = _setup_font()

# ── 易车开放平台凭证 ──────────────────────────────────────────
# 安全说明：凭证不硬编码进源码，统一从环境变量读取。
#   PowerShell : $env:YICHE_CID='xxx'; $env:YICHE_SECRET='xxx'
#   CMD        : set YICHE_CID=xxx && set YICHE_SECRET=xxx
#   Bash       : export YICHE_CID=xxx YICHE_SECRET=xxx
# 未配置时不影响「离线复现」模式（直接读取仓库自带数据集）。
CID    = os.environ.get('YICHE_CID', '').strip()
SECRET = os.environ.get('YICHE_SECRET', '').strip()
CITY   = '201'   # 北京
API    = 'https://mapi.yiche.com/web_api/car_model_api/api/v1/brand/get_brand_list'

_session = requests.Session()
_session.headers.update({
    'User-Agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                   'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'),
    'Accept': 'application/json, text/plain, */*',
    'X-Platform': 'pc',
    'X-City-Id': CITY,
    'Cid': CID,
    'Referer': 'https://car.yiche.com/',
})

def _sign(param: dict, ts: str) -> str:
    s = json.dumps(param, separators=(',', ':'))
    raw = 'cid=' + CID + '&param=' + s + SECRET + ts
    return hashlib.md5(raw.encode('utf-8')).hexdigest()

def _call(param: dict, retries=3) -> dict | None:
    ts = str(int(time.time() * 1000))
    hdrs = {'X-Sign': _sign(param, ts), 'X-Timestamp': ts}
    pstr = json.dumps(param, separators=(',', ':'))
    for i in range(retries):
        try:
            r = _session.get(API, params={'cid': CID, 'param': pstr},
                             headers=hdrs, timeout=12)
            if r.status_code == 200:
                d = r.json()
                if d.get('status') == '1' and d.get('data'):
                    return d['data']
            return None
        except Exception as e:
            if i < retries - 1:
                time.sleep(1.2 * (i + 1))
    return None

KNOWN_MASTER_IDS = [
    2, 3, 4, 5, 6, 7, 8, 9, 10, 13, 14, 15, 16, 17, 18, 19, 21, 25, 26,
    27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 39, 40, 41, 42, 43, 44,
    45, 46, 47, 48, 49, 50, 51, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61,
    62, 63, 64, 65, 66, 67, 68, 69, 70, 71, 72, 73, 74, 75, 76, 77, 78,
    79, 80, 81, 82, 83, 84, 85, 86, 87, 88, 89, 90, 91, 92, 93, 94, 95,
    96, 97, 98, 99, 100, 101, 102, 103, 104, 105, 106, 107, 108, 110, 111,
    112, 113, 114, 115, 116, 117, 118, 119, 120, 121, 122, 123, 124, 125,
    126, 127, 128, 129, 130, 131, 132, 133, 134, 135, 136, 137, 138, 139,
    140, 141, 142, 143, 144, 145, 146, 147, 148, 149, 150, 152, 153, 155,
    156, 157, 159, 161, 163, 165, 166, 167, 168, 169, 170, 171, 172, 174,
    175, 178, 179, 181, 182, 183, 185, 186, 188, 189, 195, 196, 197, 500,
    600, 700, 730, 731, 732, 733, 734, 800,
]

def _parse_price_range(s):
    """解析 '13.38-17.78' 形式的价格字符串，返回 (min, max, mid)"""
    # ══════════════════════════════════════════════
    # 【缺失值处理 ①】源头拦截：空值/无效价格 → NaN
    # ══════════════════════════════════════════════
    if not s:
        return np.nan, np.nan, np.nan   # 空字符串/None → 三列均设为 NaN
    s = str(s).strip().replace('万', '').replace(' ', '')
    # 【缺失值处理 ②】语义性缺失："暂无/待定/询价" → NaN
    if any(k in s for k in ['暂无', '待定', '询价']):
        return np.nan, np.nan, np.nan
    parts = re.split(r'[-~–—]', s)
    try:
        nums = [float(p) for p in parts if p]
        if len(nums) == 1:
            return nums[0], nums[0], nums[0]
        return nums[0], nums[-1], round((nums[0] + nums[-1]) / 2, 2)
    except:
        return np.nan, np.nan, np.nan

def crawl(target=600, only_with_price=False) -> pd.DataFrame:
    if not CID or not SECRET:
        raise RuntimeError(
            '未配置易车平台凭证。请先设置环境变量 YICHE_CID / YICHE_SECRET。\n'
            '如果只想复现分析结果、不重新爬取，请加 --offline 参数。'
        )
    print('\n' + '='*58)
    print('  易车网 车型/车系数据爬取启动')
    print('  API: mapi.yiche.com  |  城市: 北京(201)')
    print('  目标条数: >=' + str(target))
    print('='*58 + '\n')

    records = []
    master_done = set()

    scan_ids = KNOWN_MASTER_IDS[:]

    for mid in scan_ids:
        if mid in master_done:
            continue
        master_done.add(mid)

        brands_data = _call({'cityId': CITY, 'masterId': str(mid)})
        if not brands_data:
            continue

        for brand in brands_data:
            brand_id   = brand.get('id', '')
            brand_name = brand.get('name', '未知品牌')
            for s in brand.get('serialList', []):
                sid          = s.get('id', '')
                sname        = s.get('name', '')
                refer_price  = s.get('referPrice', '')
                sale_status  = s.get('saleStatus', 1)       # 1在售 2停售 3即将上市
                is_new_energy= s.get('isNewEnergy', 0)      # 1=新能源
                level1       = s.get('serialFirstLevel', '')
                level2       = s.get('serialSecondLevel', '')
                elec_range   = s.get('electricRechargeMileage', '')

                pmin, pmax, pmid = _parse_price_range(refer_price)

                _rp = str(refer_price or '')
                _sn = str(sname or '')
                _l2 = str(level2 or '')
                if is_new_energy == 1 or '新能源' in brand_name:
                    if '插混' in (_rp + _sn + _l2) or 'PHEV' in _sn.upper():
                        energy = '插电混动(PHEV)'
                    elif '增程' in (_sn + _l2):
                        energy = '增程式'
                    else:
                        energy = '纯电动(BEV)'
                else:
                    if '混动' in _sn or 'HEV' in _sn.upper():
                        energy = '油电混动(HEV)'
                    else:
                        energy = '燃油'

                status_map = {1: '在售', 2: '停售', 3: '即将上市', 0: '未知'}
                status_str = status_map.get(sale_status, '在售')

                rec = {
                    'master_id':    mid,
                    'brand_id':     brand_id,
                    'brand_name':   brand_name.replace('·新能源', '').replace('(进口)', '').strip(),
                    'serial_id':    sid,
                    'serial_name':  sname,
                    'refer_price':  refer_price,
                    'price_min':    pmin,
                    'price_max':    pmax,
                    'price_mid':    pmid,
                    'sale_status':  status_str,
                    'is_new_energy': is_new_energy,
                    'energy_type':  energy,
                    'level1':       level1,   # 轿车/SUV/MPV/皮卡/跑车/微面...
                    'level2':       level2,   # 细分级别
                    'elec_range':   elec_range,
                    'brand_raw':    brand_name,   # 含原始标注
                    'is_import':    int('进口' in brand_name),
                }
                records.append(rec)

        time.sleep(0.12)

        if len(records) % 200 == 0 and len(records) > 0:
            print('  已爬取: ' + str(len(records)) + ' 条  (masterId=' + str(mid) + ')')

    df = pd.DataFrame(records)
    print('\n[爬取完成] 原始记录: ' + str(len(df)) + ' 条\n')
    return df


def preprocess(df_raw: pd.DataFrame):
    print('='*58)
    print('  数据清洗 & 预处理')
    print('='*58)

    # ══════════════════════════════════════════════
    # 【缺失值处理 ③】统计预处理前的缺失情况（用于对比）
    # ══════════════════════════════════════════════
    price_raw = df_raw['price_mid'].dropna()
    stats_before = {
        '记录条数':     len(df_raw),
        '字段数量':     df_raw.shape[1],
        '重复车系数':   df_raw.duplicated(subset=['serial_id']).sum(),  # 【重复值处理】统计去重前重复行数
        '缺失值总数':   df_raw.isnull().sum().sum(),         # 全表缺失值汇总
        'price_mid缺失': df_raw['price_mid'].isnull().sum(), # 价格字段单独统计
        '价格范围(万)': (f'{price_raw.min():.1f} ~ {price_raw.max():.1f}'
                         if len(price_raw) > 0 else 'N/A'),
        '均值':         round(price_raw.mean(), 2) if len(price_raw) > 0 else 0,
        '中位数':       round(price_raw.median(), 2) if len(price_raw) > 0 else 0,
    }

    df = df_raw.copy()

    # ══════════════════════════════════════════════
    # 【缺失值处理 ④】删除价格缺失/无效行
    #   - notna()：过滤 NaN（无价格记录）
    #   - > 0    ：过滤价格为 0 的无效记录
    # ══════════════════════════════════════════════
    n0 = len(df)
    df = df[df['price_mid'].notna() & (df['price_mid'] > 0)]
    print('[B] 去除无价格: ' + str(n0) + ' → ' + str(len(df)))

    # ══════════════════════════════════════════════
    # 【重复值处理】按 serial_id + brand_name 联合去重
    #   - subset：以车系ID + 品牌名作为唯一标识
    #   - keep='first'：保留第一条，删除后续重复行
    #   预处理前重复数量已在 stats_before['重复车系数'] 中统计
    # ══════════════════════════════════════════════
    n1 = len(df)
    df = df.drop_duplicates(subset=['serial_id', 'brand_name'], keep='first')
    print('[C] 去重: ' + str(n1) + ' → ' + str(len(df)) + '（删除 ' + str(n1-len(df)) + ' 条）')

    # ══════════════════════════════════════════════
    # 【异常值处理】IQR×3 四分位距法过滤价格异常值
    #   公式：下界 = Q1 - 3×IQR（最低0.5万）
    #         上界 = Q3 + 3×IQR
    #   说明：系数取3（宽松），适合车价右偏分布，
    #         避免合理高价豪车被误删
    # ══════════════════════════════════════════════
    n2 = len(df)
    p = df['price_mid']
    Q1, Q3 = p.quantile(0.25), p.quantile(0.75)  # 第25、75百分位
    IQR = Q3 - Q1                                  # 四分位距
    lo = max(Q1 - 3.0 * IQR, 0.5)                 # 下界（不低于0.5万）
    hi = Q3 + 3.0 * IQR                            # 上界
    df = df[(df['price_mid'] >= lo) & (df['price_mid'] <= hi)]  # 保留范围内数据
    print('[D] IQR过滤 [' + str(round(lo,1)) + ', ' + str(round(hi,1)) + '] 万: '
          + str(n2) + ' → ' + str(len(df)) + '（删除 ' + str(n2-len(df)) + ' 条）')

    # ══════════════════════════════════════════════
    # 【缺失值处理 ⑤】类型转换：非数字强制转为 NaN
    # ══════════════════════════════════════════════
    df['price_min'] = pd.to_numeric(df['price_min'], errors='coerce')  # 非数字 → NaN
    df['price_max'] = pd.to_numeric(df['price_max'], errors='coerce')
    df['price_mid'] = pd.to_numeric(df['price_mid'], errors='coerce')

    # ══════════════════════════════════════════════
    # 【缺失值处理 ⑥】分类字段填充默认值
    #   策略：replace('')先处理空字符串，再 fillna 填充 NaN
    # ══════════════════════════════════════════════
    df['level1'] = df['level1'].replace('', '其他').fillna('其他')       # 车型大类缺失 → '其他'
    df['level2'] = df['level2'].replace('', '未分级').fillna('未分级')   # 细分级别缺失 → '未分级'
    df['energy_type'] = df['energy_type'].fillna('燃油')                  # 能源类型缺失 → '燃油'
    df['sale_status'] = df['sale_status'].fillna('在售')                  # 销售状态缺失 → '在售'

    df['price_range_span'] = (df['price_max'] - df['price_min']).clip(lower=0)
    df['price_level'] = pd.cut(
        df['price_mid'],
        bins  = [0, 8, 15, 25, 40, 60, 100, 200, 99999],
        labels= ['8万以下', '8-15万', '15-25万', '25-40万',
                 '40-60万', '60-100万', '100-200万', '200万以上']
    )
    df['is_new_energy_label'] = df['energy_type'].apply(
        lambda x: '新能源' if x in ['纯电动(BEV)', '插电混动(PHEV)', '增程式'] else '燃油/混动'
    )
    df['brand_type'] = df['brand_raw'].apply(
        lambda x: '进口' if '进口' in str(x) else '国产/合资'
    )
    df.reset_index(drop=True, inplace=True)

    p2 = df['price_mid']
    stats_after = {
        '记录条数':     len(df),
        '字段数量':     df.shape[1],
        '重复车系数':   0,
        '缺失值总数':   df[['price_mid','level1','energy_type']].isnull().sum().sum(),
        'price_mid缺失': 0,
        '价格范围(万)': f'{p2.min():.1f} ~ {p2.max():.1f}',
        '均值':         round(p2.mean(), 2),
        '中位数':       round(p2.median(), 2),
    }

    print('[完成] 最终: ' + str(len(df)) + ' 条 | ' + str(df.shape[1]) + ' 字段\n')
    return df, stats_before, stats_after


def plot_preprocess_comparison(df_raw, df_clean, sb, sa, path):
    """仿照参考图样式：对比表格 + 多子图可视化"""
    fig = plt.figure(figsize=(20, 15), facecolor='#F4F6F9')
    fig.suptitle('易车网车型数据 — 预处理前后对比分析', fontsize=20,
                 fontweight='bold', color='#2C3E50', y=0.985)

    gs = GridSpec(3, 3, figure=fig, hspace=0.52, wspace=0.38,
                  top=0.96, bottom=0.04, left=0.06, right=0.97)

    ax0 = fig.add_subplot(gs[0, :])
    ax0.axis('off')
    rows = [
        ['对比维度',        '预处理前',                         '预处理后'],
        ['原始字段数',      str(sb['字段数量']) + ' 个',        str(sa['字段数量']) + ' 个（新增 ' + str(sa['字段数量']-sb['字段数量']) + ' 个衍生字段）'],
        ['数据记录数',      str(sb['记录条数']) + ' 条',        str(sa['记录条数']) + ' 条'],
        ['重复车系数',      str(sb['重复车系数']) + ' 条',      '0 条（已清除）'],
        ['缺失值总数',      str(sb['缺失值总数']) + ' 处',      str(sa['缺失值总数']) + ' 处（已填充/删除）'],
        ['异常值',          '可能存在异常值（价格极端）',        '已用 IQR×3 方法过滤'],
        ['价格格式',        '字符串区间（如 "13.38-17.78"）',   'float 数值（min/mid/max 分列）'],
        ['价格范围（万）',  str(sb['价格范围(万)']) + ' 万',    str(sa['价格范围(万)']) + ' 万'],
        ['均值/中位数',     str(sb['均值']) + ' / ' + str(sb['中位数']) + ' 万',
                            str(sa['均值']) + ' / ' + str(sa['中位数']) + ' 万'],
        ['分析特征',        '仅原始字段',                       '含价格区间跨度、价格档次、新能源标签、进口标签'],
    ]
    cell_colors = [['#2C3E50']*3]  # header
    for i in range(len(rows)-1):
        cell_colors.append(['#EBF5FB', '#FDEBD0', '#D5F5E3'])

    tbl = ax0.table(
        cellText=rows[1:],
        colLabels=rows[0],
        cellColours=cell_colors[1:],
        cellLoc='left',
        loc='center',
        colWidths=[0.22, 0.39, 0.39],
    )
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(10.5)
    tbl.scale(1, 1.52)
    for (r, c), cell in tbl.get_celld().items():
        if r == 0:
            cell.set_facecolor('#2C3E50')
            cell.set_text_props(color='white', fontweight='bold', fontsize=11)
        cell.set_edgecolor('#C8D6E5')
    ax0.set_title('图1  数据预处理前后对比摘要表', fontsize=12, pad=6, color='#2C3E50')

    ax1 = fig.add_subplot(gs[1, 0])
    _n_raw  = sb['记录条数']
    _n_hasprice = _n_raw - sb['price_mid缺失']
    _n_dedup = _n_hasprice - sb['重复车系数']
    _n_final = sa['记录条数']
    stages = ['原始\n数据', '去无\n价格', '去重\n后', '过滤\n异常值\n最终']
    vals   = [_n_raw, _n_hasprice, _n_dedup, _n_final]
    colors_bar = ['#3498DB', '#F39C12', '#E74C3C', '#27AE60']
    bars = ax1.bar(stages, vals, color=colors_bar, width=0.55, edgecolor='white', linewidth=1)
    for bar in bars:
        ax1.text(bar.get_x()+bar.get_width()/2, bar.get_height()+_n_raw*0.012,
                 str(int(bar.get_height())), ha='center', va='bottom', fontsize=10, fontweight='bold')
    ax1.set_title('各阶段记录数量变化', fontsize=11, pad=5, fontweight='bold')
    ax1.set_ylabel('记录条数')
    ax1.set_ylim(0, _n_raw * 1.18)
    ax1.spines[['top','right']].set_visible(False)
    ax1.set_facecolor('#FAFAFA')

    ax2 = fig.add_subplot(gs[1, 1])
    pr = df_raw['price_mid'].dropna()
    pc = df_clean['price_mid'].dropna()
    bp = ax2.boxplot([pr, pc], labels=['预处理前', '预处理后'],
                     patch_artist=True, widths=0.4, notch=False,
                     flierprops=dict(marker='o', markersize=3, alpha=0.4))
    for patch, col in zip(bp['boxes'], ['#E74C3C','#27AE60']):
        patch.set_facecolor(col); patch.set_alpha(0.72)
    for med in bp['medians']:
        med.set_color('#2C3E50'); med.set_linewidth(2)
    ax2.set_title('价格分布箱线图对比（万元）', fontsize=11, pad=5, fontweight='bold')
    ax2.set_ylabel('指导价（万元）')
    ax2.spines[['top','right']].set_visible(False)
    ax2.set_facecolor('#FAFAFA')

    ax3 = fig.add_subplot(gs[1, 2])
    check_cols = ['price_mid', 'price_min', 'price_max', 'level1', 'energy_type', 'sale_status']
    miss_b = [df_raw[c].isnull().mean()*100 if c in df_raw.columns else 0 for c in check_cols]
    miss_a = [df_clean[c].isnull().mean()*100 if c in df_clean.columns else 0 for c in check_cols]
    y = np.arange(len(check_cols))
    ax3.barh(y+0.18, miss_b, height=0.32, color='#E74C3C', alpha=0.85, label='处理前')
    ax3.barh(y-0.18, miss_a, height=0.32, color='#27AE60', alpha=0.85, label='处理后')
    ax3.set_yticks(y)
    ax3.set_yticklabels(check_cols, fontsize=9)
    ax3.set_xlabel('缺失率 (%)')
    ax3.set_title('关键字段缺失率对比', fontsize=11, pad=5, fontweight='bold')
    ax3.legend(fontsize=9)
    ax3.spines[['top','right']].set_visible(False)
    ax3.set_facecolor('#FAFAFA')

    ax4 = fig.add_subplot(gs[2, 0])
    upper99 = pr.quantile(0.99)
    ax4.hist(pr[pr <= upper99], bins=45, color='#E74C3C', alpha=0.72, edgecolor='white')
    ax4.axvline(pr.median(), color='#2C3E50', lw=2, ls='--',
                label='中位数 ' + str(round(pr.median(),1)) + '万')
    ax4.set_title('价格分布直方图 — 预处理前', fontsize=11, pad=5, fontweight='bold')
    ax4.set_xlabel('指导价（万元）'); ax4.set_ylabel('频数')
    ax4.legend(fontsize=9)
    ax4.spines[['top','right']].set_visible(False)
    ax4.set_facecolor('#FAFAFA')

    ax5 = fig.add_subplot(gs[2, 1])
    ax5.hist(pc, bins=45, color='#27AE60', alpha=0.65, edgecolor='white',
             density=True, label='频率密度')
    try:
        kde = gaussian_kde(pc)
        xr = np.linspace(pc.min(), pc.max(), 300)
        ax5.plot(xr, kde(xr), color='#E74C3C', lw=2.5, label='KDE')
    except:
        pass
    ax5.axvline(pc.median(), color='#2C3E50', lw=2, ls='--',
                label='中位数 ' + str(round(pc.median(),1)) + '万')
    ax5.set_title('价格分布直方图+KDE — 预处理后', fontsize=11, pad=5, fontweight='bold')
    ax5.set_xlabel('指导价（万元）'); ax5.set_ylabel('密度')
    ax5.legend(fontsize=9)
    ax5.spines[['top','right']].set_visible(False)
    ax5.set_facecolor('#FAFAFA')

    ax6 = fig.add_subplot(gs[2, 2])
    ax6.axis('off')
    desc = pc.describe()
    tdata = [
        ['统计量', '预处理前', '预处理后'],
        ['count', str(int(pr.count())), str(int(desc['count']))],
        ['mean',  str(round(pr.mean(),2)) + ' 万', str(round(desc['mean'],2)) + ' 万'],
        ['std',   str(round(pr.std(),2)),  str(round(desc['std'],2))],
        ['min',   str(round(pr.min(),2)) + ' 万', str(round(desc['min'],2)) + ' 万'],
        ['25%',   str(round(pr.quantile(0.25),2)), str(round(desc['25%'],2))],
        ['50%',   str(round(pr.median(),2)) + ' 万', str(round(desc['50%'],2)) + ' 万'],
        ['75%',   str(round(pr.quantile(0.75),2)), str(round(desc['75%'],2))],
        ['max',   str(round(pr.max(),2)) + ' 万', str(round(desc['max'],2)) + ' 万'],
    ]
    rc = [['#2C3E50']*3] + [['#EBF5FB','#FDEBD0','#D5F5E3']]*(len(tdata)-1)
    tbl2 = ax6.table(cellText=tdata[1:], colLabels=tdata[0],
                     cellColours=rc[1:], cellLoc='center', loc='center',
                     colWidths=[0.3, 0.35, 0.35])
    tbl2.auto_set_font_size(False)
    tbl2.set_fontsize(10)
    tbl2.scale(1, 1.55)
    for (r, c), cell in tbl2.get_celld().items():
        if r == 0:
            cell.set_facecolor('#2C3E50')
            cell.set_text_props(color='white', fontweight='bold')
        cell.set_edgecolor('#C8D6E5')
    ax6.set_title('价格字段描述性统计对比', fontsize=11, pad=5, fontweight='bold')

    plt.savefig(path, dpi=150, bbox_inches='tight', facecolor='#F4F6F9')
    plt.close()
    print('[图] 预处理对比图 → ' + os.path.basename(path))


COLOR_MAP = {
    '燃油':         '#E74C3C',
    '油电混动(HEV)':'#F39C12',
    '纯电动(BEV)':  '#3498DB',
    '插电混动(PHEV)':'#27AE60',
    '增程式':       '#9B59B6',
}

def _save(fig, path):
    fig.savefig(path, dpi=150, bbox_inches='tight')
    plt.close(fig)
    print('  [OK] ' + os.path.basename(path))


def plot_01_brand_count(df, path):
    top = df.groupby('brand_name').size().sort_values(ascending=False).head(20)
    fig, ax = plt.subplots(figsize=(14, 7), facecolor='#F8F9FA')
    cmap = plt.cm.Blues(np.linspace(0.35, 0.9, len(top)))
    bars = ax.bar(top.index, top.values, color=cmap, edgecolor='white', linewidth=0.8)
    for bar in bars:
        ax.text(bar.get_x()+bar.get_width()/2, bar.get_height()+0.3,
                str(int(bar.get_height())), ha='center', va='bottom', fontsize=9)
    ax.set_title('各品牌车型/车系数量 Top 20', fontsize=16, fontweight='bold', pad=10)
    ax.set_ylabel('车型数量（款）'); ax.tick_params(axis='x', rotation=35)
    ax.spines[['top','right']].set_visible(False)
    ax.set_facecolor('#FAFAFA')
    fig.tight_layout()
    _save(fig, path)


def plot_02_brand_avg_price(df, path):
    stats = (df.groupby('brand_name')['price_mid']
               .agg(['mean','count'])
               .query('count >= 2')
               .sort_values('mean', ascending=False)
               .head(20))
    fig, ax = plt.subplots(figsize=(14, 7), facecolor='#F8F9FA')
    cmap = plt.cm.RdYlGn_r(np.linspace(0.05, 0.9, len(stats)))
    bars = ax.bar(stats.index, stats['mean'], color=cmap, edgecolor='white')
    for bar in bars:
        ax.text(bar.get_x()+bar.get_width()/2, bar.get_height()+0.5,
                str(round(bar.get_height(),1)), ha='center', va='bottom', fontsize=9)
    ax.set_title('各品牌平均指导价 Top 20（万元）', fontsize=16, fontweight='bold', pad=10)
    ax.set_ylabel('平均价格（万元）'); ax.tick_params(axis='x', rotation=35)
    ax.spines[['top','right']].set_visible(False)
    ax.set_facecolor('#FAFAFA')
    fig.tight_layout()
    _save(fig, path)


def plot_03_price_level_pie(df, path):
    counts = df['price_level'].value_counts().sort_index()
    counts = counts[counts > 0]
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 7), facecolor='#F8F9FA')
    colors = ['#3498DB','#27AE60','#F39C12','#E74C3C','#9B59B6','#1ABC9C','#E67E22','#2C3E50']
    wedges, texts, autos = ax1.pie(
        counts.values, labels=counts.index, autopct='%1.1f%%',
        colors=colors[:len(counts)],
        wedgeprops=dict(edgecolor='white', linewidth=1.5),
        textprops={'fontsize':10},
        startangle=140,
    )
    for at in autos: at.set_fontsize(9)
    ax1.set_title('车型价格区间分布（饼图）', fontsize=13, fontweight='bold', pad=8)

    ax2.barh(counts.index, counts.values, color=colors[:len(counts)], edgecolor='white')
    for i, v in enumerate(counts.values):
        ax2.text(v+1, i, str(v)+'款', va='center', fontsize=10)
    ax2.set_title('价格区间数量（柱状图）', fontsize=13, fontweight='bold', pad=8)
    ax2.set_xlabel('车型数量（款）')
    ax2.spines[['top','right']].set_visible(False)
    ax2.set_facecolor('#FAFAFA')

    fig.suptitle('车型价格区间分布分析', fontsize=16, fontweight='bold', y=1.02)
    fig.tight_layout()
    _save(fig, path)


def plot_04_energy_type(df, path):
    ec = df['energy_type'].value_counts()
    label_map = df['is_new_energy_label'].value_counts()
    fig, axes = plt.subplots(1, 3, figsize=(18, 7), facecolor='#F8F9FA')

    # 大分类饼
    ax = axes[0]
    colors2 = ['#27AE60', '#E74C3C']
    ax.pie(label_map.values, labels=label_map.index, autopct='%1.1f%%',
           colors=colors2, wedgeprops=dict(edgecolor='white', linewidth=2),
           textprops={'fontsize':11})
    ax.set_title('新能源 vs 燃油/混动 占比', fontsize=12, fontweight='bold')

    # 细分能源类型柱状
    ax2 = axes[1]
    bar_colors = [COLOR_MAP.get(n, '#BDC3C7') for n in ec.index]
    ax2.bar(ec.index, ec.values, color=bar_colors, edgecolor='white')
    for i, v in enumerate(ec.values):
        ax2.text(i, v+1, str(v), ha='center', fontsize=10)
    ax2.set_title('能源类型细分统计', fontsize=12, fontweight='bold')
    ax2.set_ylabel('车型数量（款）')
    ax2.tick_params(axis='x', rotation=20)
    ax2.spines[['top','right']].set_visible(False)
    ax2.set_facecolor('#FAFAFA')

    # 各能源类型平均价格
    ax3 = axes[2]
    avg_e = df.groupby('energy_type')['price_mid'].mean().sort_values(ascending=False)
    bar_colors3 = [COLOR_MAP.get(n, '#BDC3C7') for n in avg_e.index]
    ax3.bar(avg_e.index, avg_e.values, color=bar_colors3, edgecolor='white')
    for i, v in enumerate(avg_e.values):
        ax3.text(i, v+0.5, str(round(v,1))+'万', ha='center', fontsize=9)
    ax3.set_title('各能源类型平均价格（万元）', fontsize=12, fontweight='bold')
    ax3.set_ylabel('平均价格（万元）')
    ax3.tick_params(axis='x', rotation=20)
    ax3.spines[['top','right']].set_visible(False)
    ax3.set_facecolor('#FAFAFA')

    fig.suptitle('新能源 vs 燃油车型深度分析', fontsize=16, fontweight='bold', y=1.02)
    fig.tight_layout()
    _save(fig, path)


def plot_05_level_price(df, path):
    tmp = (df[df['level1'] != '其他']
           .groupby('level1')['price_mid']
           .agg(['mean','median','count','std'])
           .query('count >= 3')
           .sort_values('mean'))
    fig, axes = plt.subplots(1, 2, figsize=(16, 7), facecolor='#F8F9FA')

    ax = axes[0]
    x = np.arange(len(tmp))
    ax.barh(x-0.2, tmp['mean'],   height=0.35, color='#3498DB', alpha=0.88, label='均值')
    ax.barh(x+0.2, tmp['median'], height=0.35, color='#E74C3C', alpha=0.88, label='中位数')
    ax.set_yticks(x); ax.set_yticklabels(tmp.index)
    ax.set_xlabel('指导价（万元）')
    ax.set_title('各车型大类 均值/中位数 价格对比', fontsize=12, fontweight='bold')
    ax.legend(fontsize=10)
    ax.spines[['top','right']].set_visible(False)
    ax.set_facecolor('#FAFAFA')

    # 车型数量横条
    ax2 = axes[1]
    cmap = plt.cm.Oranges(np.linspace(0.4, 0.9, len(tmp)))
    ax2.barh(np.arange(len(tmp)), tmp['count'].values, color=cmap, edgecolor='white')
    ax2.set_yticks(np.arange(len(tmp))); ax2.set_yticklabels(tmp.index)
    for i, v in enumerate(tmp['count']):
        ax2.text(v+0.5, i, str(v)+'款', va='center', fontsize=10)
    ax2.set_xlabel('车型数量（款）')
    ax2.set_title('各车型大类车款数量', fontsize=12, fontweight='bold')
    ax2.spines[['top','right']].set_visible(False)
    ax2.set_facecolor('#FAFAFA')

    fig.suptitle('车型级别 × 价格 × 数量 分析', fontsize=16, fontweight='bold', y=1.02)
    fig.tight_layout()
    _save(fig, path)


def plot_06_price_kde(df, path):
    fig, axes = plt.subplots(1, 2, figsize=(16, 7), facecolor='#F8F9FA')
    pc = df['price_mid'].dropna()

    ax = axes[0]
    ax.hist(pc, bins=55, color='#3498DB', alpha=0.62, edgecolor='white',
            density=True, label='频率密度')
    try:
        kde = gaussian_kde(pc)
        xr = np.linspace(pc.min(), pc.max(), 300)
        ax.plot(xr, kde(xr), color='#E74C3C', lw=2.5, label='KDE密度曲线')
    except:
        pass
    ax.axvline(pc.mean(),   color='#F39C12', ls='--', lw=2, label=f'均值 {pc.mean():.1f}万')
    ax.axvline(pc.median(), color='#27AE60', ls='--', lw=2, label=f'中位数 {pc.median():.1f}万')
    ax.set_title('全量车型价格分布（KDE）', fontsize=13, fontweight='bold')
    ax.set_xlabel('指导价（万元）'); ax.set_ylabel('密度')
    ax.legend(fontsize=10)
    ax.spines[['top','right']].set_visible(False); ax.set_facecolor('#FAFAFA')

    # 分能源类型 KDE
    ax2 = axes[1]
    for etype, group in df.groupby('energy_type'):
        vals = group['price_mid'].dropna()
        if len(vals) < 10:
            continue
        try:
            kde2 = gaussian_kde(vals)
            xr2 = np.linspace(vals.min(), vals.max(), 200)
            ax2.plot(xr2, kde2(xr2), lw=2, label=etype + '(' + str(len(vals)) + '款)',
                     color=COLOR_MAP.get(etype, None))
        except:
            pass
    ax2.set_title('各能源类型价格 KDE 分布', fontsize=13, fontweight='bold')
    ax2.set_xlabel('指导价（万元）'); ax2.set_ylabel('密度')
    ax2.legend(fontsize=9); ax2.spines[['top','right']].set_visible(False)
    ax2.set_facecolor('#FAFAFA')

    fig.suptitle('车型价格分布深度分析', fontsize=16, fontweight='bold', y=1.02)
    fig.tight_layout()
    _save(fig, path)


def plot_07_energy_box(df, path):
    fig, axes = plt.subplots(1, 2, figsize=(16, 7), facecolor='#F8F9FA')

    # 大分类箱线
    ax = axes[0]
    groups = df.groupby('is_new_energy_label')['price_mid'].apply(list).to_dict()
    labels = list(groups.keys())
    bp = ax.boxplot([groups[k] for k in labels], labels=labels,
                    patch_artist=True, widths=0.4,
                    flierprops=dict(marker='o', markersize=3, alpha=0.3))
    for patch, col in zip(bp['boxes'], ['#27AE60','#E74C3C'][:len(labels)]):
        patch.set_facecolor(col); patch.set_alpha(0.7)
    for med in bp['medians']:
        med.set_color('#2C3E50'); med.set_linewidth(2)
    ax.set_title('新能源 vs 燃油/混动 价格分布箱线图', fontsize=12, fontweight='bold')
    ax.set_ylabel('指导价（万元）')
    ax.spines[['top','right']].set_visible(False); ax.set_facecolor('#FAFAFA')

    # 细分能源类型散点+箱线
    ax2 = axes[1]
    etypes = df['energy_type'].value_counts().index.tolist()
    data2 = [df[df['energy_type']==e]['price_mid'].dropna().tolist() for e in etypes]
    bp2 = ax2.boxplot(data2, labels=etypes, patch_artist=True, widths=0.4,
                      flierprops=dict(marker='o', markersize=3, alpha=0.3))
    for patch, etype in zip(bp2['boxes'], etypes):
        patch.set_facecolor(COLOR_MAP.get(etype, '#BDC3C7'))
        patch.set_alpha(0.75)
    for med in bp2['medians']:
        med.set_color('#2C3E50'); med.set_linewidth(2)
    ax2.set_title('各能源类型价格分布细分', fontsize=12, fontweight='bold')
    ax2.set_ylabel('指导价（万元）')
    ax2.tick_params(axis='x', rotation=20)
    ax2.spines[['top','right']].set_visible(False); ax2.set_facecolor('#FAFAFA')

    fig.suptitle('燃油 vs 新能源 价格对比分析', fontsize=16, fontweight='bold', y=1.02)
    fig.tight_layout()
    _save(fig, path)


def plot_08_brand_bubble(df, path):
    stats = (df.groupby('brand_name')
               .agg(count=('serial_name','count'),
                    avg_price=('price_mid','mean'),
                    price_span=('price_mid', lambda x: x.max()-x.min()))
               .query('count >= 3')
               .sort_values('count', ascending=False)
               .head(30))
    if stats.empty:
        return
    fig, ax = plt.subplots(figsize=(14, 9), facecolor='#F8F9FA')
    sc = ax.scatter(stats['count'], stats['avg_price'],
                    s=stats['price_span']*12+80,
                    c=stats['avg_price'], cmap='RdYlGn_r',
                    alpha=0.78, edgecolors='white', linewidth=1)
    for _, row in stats.iterrows():
        ax.annotate(row.name, (row['count'], row['avg_price']),
                    textcoords='offset points', xytext=(5, 4), fontsize=8.5)
    cbar = plt.colorbar(sc, ax=ax)
    cbar.set_label('平均价格（万元）', fontsize=10)
    ax.set_title('品牌 车型数量 vs 平均价格（气泡大小=价格跨度）',
                 fontsize=15, fontweight='bold', pad=10)
    ax.set_xlabel('车型数量（款）'); ax.set_ylabel('平均价格（万元）')
    ax.spines[['top','right']].set_visible(False); ax.set_facecolor('#FAFAFA')
    fig.tight_layout()
    _save(fig, path)


def plot_09_level2_heatmap(df, path):
    """车型细分级别 × 能源类型 热力图"""
    # 取 level2 中较常见的
    top_l2 = df['level2'].value_counts().head(12).index.tolist()
    top_et = df['energy_type'].value_counts().head(5).index.tolist()
    sub = df[df['level2'].isin(top_l2) & df['energy_type'].isin(top_et)]
    if len(sub) < 10:
        return
    pivot = sub.pivot_table(values='price_mid', index='level2',
                            columns='energy_type', aggfunc='mean', fill_value=0)
    # 仅保留有数据的列
    pivot = pivot[pivot.columns[pivot.sum() > 0]]
    if pivot.empty:
        return

    import matplotlib.colors as mcolors
    fig, ax = plt.subplots(figsize=(14, 8), facecolor='#F8F9FA')
    im = ax.imshow(pivot.values, cmap='YlOrRd', aspect='auto')
    ax.set_xticks(np.arange(len(pivot.columns)))
    ax.set_yticks(np.arange(len(pivot.index)))
    ax.set_xticklabels(pivot.columns, fontsize=10)
    ax.set_yticklabels(pivot.index, fontsize=10)
    plt.colorbar(im, ax=ax, label='平均价格（万元）')
    # 标注数值
    for i in range(len(pivot.index)):
        for j in range(len(pivot.columns)):
            v = pivot.values[i, j]
            if v > 0:
                ax.text(j, i, str(round(v,1)), ha='center', va='center',
                        fontsize=8.5, color='white' if v > pivot.values.max()*0.6 else '#2C3E50')
    ax.set_title('细分级别 × 能源类型 平均价格热力图（万元）',
                 fontsize=15, fontweight='bold', pad=10)
    ax.set_xlabel('能源类型'); ax.set_ylabel('细分级别')
    fig.tight_layout()
    _save(fig, path)


def plot_10_dashboard(df, path):
    """综合 Dashboard（深色主题）"""
    fig = plt.figure(figsize=(22, 14), facecolor='#0D1117')
    fig.suptitle('易车网 车型数据可视化分析 Dashboard',
                 fontsize=22, fontweight='bold', color='white', y=0.99)
    DARK = '#161B22'
    TEXT = 'white'
    gs = GridSpec(3, 4, figure=fig, hspace=0.48, wspace=0.36,
                  top=0.95, bottom=0.04, left=0.05, right=0.97)

    # KPI 卡片
    ne_pct = (df['is_new_energy_label']=='新能源').mean()*100
    kpis = [
        ('总车系数', str(len(df)) + ' 款', '#3498DB'),
        ('品牌数量', str(df['brand_name'].nunique()) + ' 个', '#27AE60'),
        ('均价', str(round(df['price_mid'].mean(),1)) + ' 万', '#E74C3C'),
        ('新能源占比', str(round(ne_pct,1)) + '%', '#F39C12'),
    ]
    for i, (title, val, col) in enumerate(kpis):
        ax = fig.add_subplot(gs[0, i])
        ax.set_facecolor(DARK); ax.axis('off')
        ax.set_xlim(0,1); ax.set_ylim(0,1)
        ax.add_patch(plt.Rectangle((0.05,0.08),0.9,0.84, linewidth=2.5,
                                    edgecolor=col, facecolor='none', alpha=0.9))
        ax.text(0.5, 0.60, val, ha='center', va='center',
                fontsize=22, color=col, fontweight='bold')
        ax.text(0.5, 0.25, title, ha='center', va='center', fontsize=12, color=TEXT)

    # 品牌 Top10 横条
    ax5 = fig.add_subplot(gs[1, :2])
    ax5.set_facecolor(DARK)
    top10 = df.groupby('brand_name').size().sort_values(ascending=False).head(10)
    bars = ax5.barh(top10.index[::-1], top10.values[::-1],
                    color=plt.cm.Blues(np.linspace(0.4,0.9,10)), edgecolor='none')
    for bar in bars:
        ax5.text(bar.get_width()+0.3, bar.get_y()+bar.get_height()/2,
                 str(int(bar.get_width())), va='center', color=TEXT, fontsize=9)
    ax5.set_title('车型数量 Top 10 品牌', color=TEXT, fontsize=13, fontweight='bold', pad=6)
    ax5.tick_params(colors=TEXT); ax5.spines[:].set_color('#30363D')

    # 价格区间饼
    ax6 = fig.add_subplot(gs[1, 2:])
    ax6.set_facecolor(DARK)
    counts = df['price_level'].value_counts().sort_index()
    counts = counts[counts > 0]
    col8 = ['#3498DB','#27AE60','#F39C12','#E74C3C','#9B59B6','#1ABC9C','#E67E22','#2C3E50']
    ax6.pie(counts.values, labels=counts.index, autopct='%1.1f%%',
            colors=col8[:len(counts)],
            wedgeprops=dict(edgecolor='#0D1117', linewidth=1.2),
            textprops={'color':TEXT,'fontsize':9})
    ax6.set_title('价格区间分布', color=TEXT, fontsize=13, fontweight='bold', pad=6)
    ax6.set_facecolor(DARK)

    # 价格 KDE
    ax7 = fig.add_subplot(gs[2, :2])
    ax7.set_facecolor(DARK)
    pc = df['price_mid'].dropna()
    ax7.hist(pc, bins=55, color='#3498DB', alpha=0.45, edgecolor='none', density=True)
    try:
        kde = gaussian_kde(pc)
        xr = np.linspace(pc.min(), pc.max(), 300)
        ax7.plot(xr, kde(xr), color='#E74C3C', lw=2.5)
    except:
        pass
    ax7.axvline(pc.mean(),   color='#F39C12', ls='--', lw=1.8, label='均值 '+str(round(pc.mean(),1))+'万')
    ax7.axvline(pc.median(), color='#27AE60', ls='--', lw=1.8, label='中位 '+str(round(pc.median(),1))+'万')
    ax7.set_title('价格分布 KDE', color=TEXT, fontsize=13, fontweight='bold', pad=6)
    ax7.tick_params(colors=TEXT); ax7.spines[:].set_color('#30363D')
    ax7.legend(fontsize=9, facecolor=DARK, labelcolor=TEXT)

    # 能源类型箱线
    ax8 = fig.add_subplot(gs[2, 2:])
    ax8.set_facecolor(DARK)
    etypes = df['energy_type'].value_counts().index.tolist()
    data_e = [df[df['energy_type']==e]['price_mid'].dropna().tolist() for e in etypes]
    bp = ax8.boxplot(data_e, labels=etypes, patch_artist=True, widths=0.4,
                     flierprops=dict(marker='o', markersize=2, alpha=0.3))
    for patch, etype in zip(bp['boxes'], etypes):
        patch.set_facecolor(COLOR_MAP.get(etype,'#BDC3C7')); patch.set_alpha(0.78)
    for med in bp['medians']:
        med.set_color('white'); med.set_linewidth(2)
    ax8.set_title('各能源类型价格箱线图', color=TEXT, fontsize=13, fontweight='bold', pad=6)
    ax8.tick_params(colors=TEXT, axis='both')
    ax8.tick_params(axis='x', rotation=15)
    ax8.spines[:].set_color('#30363D')

    plt.savefig(path, dpi=150, bbox_inches='tight', facecolor='#0D1117')
    plt.close(fig)
    print('  [OK] ' + os.path.basename(path))


def plot_all_charts(df, chart_dir):
    os.makedirs(chart_dir, exist_ok=True)
    print('\n[图表] 开始生成多维分析图...')
    plot_01_brand_count   (df, chart_dir + '/01_brand_count.png')
    plot_02_brand_avg_price(df, chart_dir + '/02_brand_avg_price.png')
    plot_03_price_level_pie(df, chart_dir + '/03_price_level_pie.png')
    plot_04_energy_type   (df, chart_dir + '/04_energy_type.png')
    plot_05_level_price   (df, chart_dir + '/05_level_price.png')
    plot_06_price_kde     (df, chart_dir + '/06_price_kde.png')
    plot_07_energy_box    (df, chart_dir + '/07_energy_box.png')
    plot_08_brand_bubble  (df, chart_dir + '/08_brand_bubble.png')
    plot_09_level2_heatmap(df, chart_dir + '/09_level2_heatmap.png')
    plot_10_dashboard     (df, chart_dir + '/10_dashboard.png')
    print('[图表] 全部生成完毕！路径: ' + chart_dir)


def main():
    import argparse
    parser = argparse.ArgumentParser(description='易车网车型数据爬取与可视化分析')
    parser.add_argument('--offline', action='store_true',
                        help='跳过爬取，直接使用仓库自带数据集 data/yiche_raw.csv 复现全部分析')
    args = parser.parse_args()

    HERE      = os.path.dirname(os.path.abspath(__file__))
    BASE_DIR  = os.path.join(HERE, 'yiche_output')
    os.makedirs(BASE_DIR, exist_ok=True)
    RAW_CSV   = BASE_DIR + '/yiche_raw.csv'
    CLEAN_CSV = BASE_DIR + '/yiche_clean.csv'
    PREPROC   = BASE_DIR + '/00_preprocess_comparison.png'
    CHART_DIR = BASE_DIR + '/charts'
    RAW_SRC   = os.path.join(HERE, 'data', 'yiche_raw.csv')

    # ── Step 1: 获取原始数据（联网爬取 或 离线载入）──
    if args.offline:
        if not os.path.exists(RAW_SRC):
            print('[错误] 未找到离线数据集: ' + RAW_SRC)
            sys.exit(1)
        df_raw = pd.read_csv(RAW_SRC)
        print('[离线] 载入仓库数据集 → ' + RAW_SRC + '  (' + str(len(df_raw)) + ' 条)')
        df_raw.to_csv(RAW_CSV, index=False, encoding='utf-8-sig')
    else:
        df_raw = crawl(target=3000, only_with_price=False)   # 爬全量，清洗后保证500+
        if len(df_raw) < 50:
            print('[错误] 数据量不足，请检查网络或稍后重试。')
            sys.exit(1)
        df_raw.to_csv(RAW_CSV, index=False, encoding='utf-8-sig')
        print('[保存] 原始数据 → ' + RAW_CSV + '  (' + str(len(df_raw)) + ' 条)')

    # ── Step 2: 预处理 ──
    df_clean, sb, sa = preprocess(df_raw)
    df_clean.to_csv(CLEAN_CSV, index=False, encoding='utf-8-sig')
    print('[保存] 清洁数据 → ' + CLEAN_CSV + '  (' + str(len(df_clean)) + ' 条)')

    # ── Step 3: 预处理对比图 ──
    print('\n[图] 绘制预处理对比图...')
    plot_preprocess_comparison(df_raw, df_clean, sb, sa, PREPROC)

    # ── Step 4: 多维可视化 ──
    plot_all_charts(df_clean, CHART_DIR)

    # ── 汇总 ──
    print('\n' + '='*58)
    print('  全流程完成!')
    print('  原始: ' + str(len(df_raw)) + ' 条  →  清洁: ' + str(len(df_clean)) + ' 条')
    print('  输出目录: ' + BASE_DIR)
    print('='*58)


if __name__ == '__main__':
    main()
