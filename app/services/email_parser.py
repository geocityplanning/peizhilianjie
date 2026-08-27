import re
from datetime import datetime
from typing import Any
from urllib.parse import quote

TITLE_RE = re.compile(r'【([^】]+)】')
URL_RE = re.compile(r'https?://[^\s\]\)）>]+')
SCHEME_RE = re.compile(r'(mcloud://[^\s\]\)）>]+|com\.greenpoint://[^\s\]\)）>]+)')


def _clean(text: str) -> str:
    return text.replace('\\_', '_').replace('\\&', '&').strip()


def _first_url_after(label: str, text: str) -> str:
    idx = text.find(label)
    if idx < 0:
        return ''
    match = URL_RE.search(text[idx:])
    return _clean(match.group(0)) if match else ''


def _infer_channel_base(text: str) -> str:
    province_codes = {
        '甘肃': 'gs', '广东': 'gd', '广西': 'gx', '北京': 'bj', '上海': 'sh', '天津': 'tj',
        '重庆': 'cq', '江苏': 'js', '浙江': 'zj', '安徽': 'ah', '福建': 'fj', '江西': 'jx',
        '山东': 'sd', '河南': 'ha', '湖北': 'hb', '湖南': 'hn', '海南': 'hi', '四川': 'sc',
        '贵州': 'gz', '云南': 'yn', '西藏': 'xz', '陕西': 'sn', '青海': 'qh', '宁夏': 'nx',
        '新疆': 'xj', '河北': 'he', '山西': 'sx', '辽宁': 'ln', '吉林': 'jl', '黑龙江': 'hlj',
        '内蒙古': 'nmg',
    }
    for name, code in province_codes.items():
        if name in text:
            return f'{code}{datetime.now().strftime("%m%d")}'
    return f'qd{datetime.now().strftime("%m%d")}'


def _infer_group(text: str) -> str:
    if '瀑布流' in text or '首页' in text or '中国移动APP' in text:
        return '10033'
    return ''


def parse_customer_email(raw_text: str) -> dict[str, Any]:
    text = _clean(raw_text or '')
    title_match = TITLE_RE.search(text)
    bracket_title = title_match.group(1).strip() if title_match else ''

    parts = [p.strip() for p in re.split(r'[-－—]', bracket_title, maxsplit=1)] if bracket_title else []
    type_hint = parts[0] if parts else ''
    activity_name = parts[1] if len(parts) > 1 else bracket_title

    original_url = _first_url_after('页面原始链接', text) or (URL_RE.search(text).group(0) if URL_RE.search(text) else '')
    fallback_url = _first_url_after('兜底跳转链接', text) or original_url
    scheme_match = SCHEME_RE.search(text)
    scheme = _clean(scheme_match.group(1)) if scheme_match else ''

    application_type = '掌厅' if ('掌厅' in text and '云盘' not in type_hint) else '云盘'
    settlement_type = '云盘' if ('云盘' in text or 'mcloud://' in text) else '在线'
    business_object = '中国移动云盘' if settlement_type == '云盘' else '中国移动APP'
    app_name_preview = f'{business_object}-{activity_name}' if activity_name else business_object

    parsed = {
        'business_object': business_object,
        'activity_name': activity_name,
        'actual_channel_name': '',
        'channel_base_name': _infer_channel_base(text),
        'application_type': application_type,
        'jump_address': scheme,
        'resource_fallback_page': fallback_url,
        'settlement_type': settlement_type,
        'group_name': _infer_group(text),
        'original_url': original_url,
        'scene': '中国移动APP-首页-瀑布流点位' if '瀑布流' in text else '',
        'template_hint': f'建议按“{business_object}”模板复制创建，可由操作人员调整。',
        'app_name_preview': app_name_preview,
    }

    ledger_row = build_ledger_row(parsed, {})
    return {'parsed': parsed, 'ledger_row': ledger_row}


def build_ledger_row(parsed: dict[str, Any], result: dict[str, Any] | None = None) -> dict[str, Any]:
    result = result or {}
    row_data = result.get('row_data') or {}
    cloud_link = result.get('cloud_app_link') or row_data.get('长连接') or ''
    headers = [
        '序号', '填写时间', '需求', '业务类型', '活动名称', '原始链接', 'scheme', '渠道名',
        '应用配置名称', '资源不足兜底页', '配置类型', '归属省份', '跳转地址', '分组',
        '云化链接（capp开头）', '状态', '验证结果', 'app_id'
    ]
    values = [
        '', datetime.now().strftime('%Y-%m-%d'), '', parsed.get('business_object', ''),
        parsed.get('activity_name', ''), parsed.get('original_url', ''), parsed.get('jump_address', ''),
        parsed.get('actual_channel_name') or parsed.get('channel_base_name', ''),
        parsed.get('app_name_preview', ''), parsed.get('resource_fallback_page', ''),
        parsed.get('settlement_type', ''), '', parsed.get('jump_address', ''), parsed.get('group_name', ''),
        cloud_link, '成功' if result.get('success') else '', '', row_data.get('ID') or result.get('app_id') or ''
    ]
    return {'headers': headers, 'values': values, 'tsv': '\t'.join(str(v or '') for v in values)}
