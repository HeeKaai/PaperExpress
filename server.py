#!/usr/bin/env python3
"""
PaperExpress Backend Server
处理 arXiv API 调用和 LLM 翻译请求
"""

import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from urllib import request, error, parse
from urllib.parse import urlparse
import ssl
import sys
import os
import threading
import queue
import time
import hashlib
import ast

# 缓存和提示词版本，用于避免不同提示词/数据结构之间误命中
CACHE_SCHEMA_VERSION = 2
TRANSLATION_PROMPT_VERSION = "translation-json-v1"
STATIC_ASSET_VERSION = "20260609-trend-llm-v2"

BANNED_TREND_LABELS = {
    "a", "an", "the", "and", "or", "of", "to", "in", "on", "for", "with", "by",
    "from", "as", "at", "is", "are", "was", "were", "be", "been", "being",
    "can", "could", "may", "might", "will", "would", "should", "must", "not",
    "no", "this", "that", "these", "those", "their", "its", "our", "your",
    "paper", "papers", "study", "studies", "work", "works", "approach",
    "approaches", "method", "methods", "model", "models", "data", "dataset",
    "datasets", "learning", "training", "testing", "evaluation", "benchmark",
    "benchmarks", "result", "results", "performance", "analysis", "existing",
    "present", "proposed", "novel", "new", "effective", "efficient", "robust",
    "execution", "access", "control", "design", "system", "systems"
}

# 项目数据目录
DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')
PAPERS_DIR = os.path.join(DATA_DIR, 'papers')
INTENSIVE_DIR = os.path.join(DATA_DIR, 'intensive')
INDEX_FILE = os.path.join(DATA_DIR, 'index.json')


def ensure_data_dirs():
    """确保数据目录存在"""
    for d in [DATA_DIR, PAPERS_DIR, INTENSIVE_DIR]:
        if not os.path.exists(d):
            os.makedirs(d, exist_ok=True)


def load_index():
    """加载索引文件"""
    ensure_data_dirs()
    if not os.path.exists(INDEX_FILE):
        return {"papers": {}, "intensive": {}}
    try:
        with open(INDEX_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {"papers": {}, "intensive": {}}


def save_index(index):
    """保存索引文件"""
    ensure_data_dirs()
    with open(INDEX_FILE, 'w', encoding='utf-8') as f:
        json.dump(index, f, ensure_ascii=False, indent=2)


def safe_print(text):
    """兼容 Windows GBK 控制台，避免启动横幅中的 emoji 导致服务退出"""
    try:
        print(text)
    except UnicodeEncodeError:
        encoding = sys.stdout.encoding or 'utf-8'
        safe_text = str(text).encode(encoding, errors='replace').decode(encoding, errors='replace')
        print(safe_text)


def compute_hash(*parts):
    """计算配置的哈希值（用于缓存键）"""
    combined = '|'.join(str(p) for p in parts)
    return hashlib.md5(combined.encode('utf-8')).hexdigest()[:16]


def normalize_keywords(keywords):
    """规范化自由关键词，确保缓存键和查询条件稳定"""
    if isinstance(keywords, list):
        keywords = ' '.join(str(item) for item in keywords)
    keywords = str(keywords or '').strip()
    return re.sub(r'\s+', ' ', keywords)


def current_utc_date_bucket():
    """按 UTC 日期分桶，避免“最近 N 天”跨天仍命中过期缓存"""
    return datetime.utcnow().strftime('%Y-%m-%d')


def paper_config_hash(categories, time_range, max_papers, keywords='', model_name='',
                      prompt_version=TRANSLATION_PROMPT_VERSION, paper_ids=None,
                      date_bucket=None):
    """论文速递配置的哈希值"""
    cats = ','.join(sorted(categories))
    normalized_keywords = normalize_keywords(keywords)
    ids = ','.join(sorted(paper_ids or []))
    bucket = date_bucket or current_utc_date_bucket()
    return compute_hash(
        CACHE_SCHEMA_VERSION,
        cats,
        normalized_keywords,
        time_range,
        max_papers,
        model_name,
        prompt_version,
        bucket,
        ids
    )


def intensive_hash(arXiv_id, paper_title):
    """精读记录的哈希值（基于论文ID和标题）"""
    return compute_hash(arXiv_id, paper_title[:100])


def get_papers_record(hash_key):
    """获取论文速递记录"""
    path = os.path.join(PAPERS_DIR, f"{hash_key}.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def save_papers_record(hash_key, record):
    """保存论文速递记录"""
    ensure_data_dirs()
    path = os.path.join(PAPERS_DIR, f"{hash_key}.json")
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(record, f, ensure_ascii=False, indent=2)

    paper_count = record.get('count')
    if paper_count is None:
        paper_count = len(record.get('papers', []))

    # 更新索引
    index = load_index()
    index['papers'][hash_key] = {
        "title": record.get('title', '未命名'),
        "created": record.get('created', datetime.now().isoformat()),
        "count": paper_count,
        "keywords": record.get('keywords', '')
    }
    save_index(index)


def get_intensive_record(hash_key):
    """获取精读记录"""
    path = os.path.join(INTENSIVE_DIR, f"{hash_key}.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def save_intensive_record(hash_key, record):
    """保存精读记录"""
    ensure_data_dirs()
    path = os.path.join(INTENSIVE_DIR, f"{hash_key}.json")
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(record, f, ensure_ascii=False, indent=2)

    # 更新索引
    index = load_index()
    index['intensive'][hash_key] = {
        "paperTitle": record.get('paperTitle', '')[:80],
        "arXivId": record.get('arXivId', ''),
        "created": record.get('created', datetime.now().isoformat())
    }
    save_index(index)


def delete_papers_record(hash_key):
    """删除论文速递记录"""
    path = os.path.join(PAPERS_DIR, f"{hash_key}.json")
    if os.path.exists(path):
        os.remove(path)
    index = load_index()
    if hash_key in index['papers']:
        del index['papers'][hash_key]
        save_index(index)


def delete_intensive_record(hash_key):
    """删除精读记录"""
    path = os.path.join(INTENSIVE_DIR, f"{hash_key}.json")
    if os.path.exists(path):
        os.remove(path)
    index = load_index()
    if hash_key in index['intensive']:
        del index['intensive'][hash_key]
        save_index(index)


def list_history():
    """列出所有历史记录"""
    index = load_index()
    # 附加完整记录信息
    for key, meta in index['papers'].items():
        record = get_papers_record(key)
        if record:
            meta['papers'] = record.get('papers', [])
            meta['stats'] = record.get('stats', {})
    return index

# arXiv API 命名空间
ARXIV_NS = {
    'atom': 'http://www.w3.org/2005/Atom',
    'arxiv': 'http://arxiv.org/schemas/atom'
}

# 只保留计算机科学相关分类
ARXIV_CATEGORIES = {
    # 人工智能相关
    "cs.AI": "人工智能 (Artificial Intelligence)",
    "cs.CL": "自然语言处理 (Computation and Language)",
    "cs.CV": "计算机视觉 (Computer Vision and Pattern Recognition)",
    "cs.LG": "机器学习 (Machine Learning)",
    "cs.RO": "机器人学 (Robotics)",
    "cs.CY": "计算与社会 (Computers and Society)",
    "cs.HC": "人机交互 (Human-Computer Interaction)",
    "cs.MA": "多代理系统 (Multiagent Systems)",
    "cs.SC": "科学计算 (Scientific Computing)",
    "cs.ET": "新兴技术 (Emerging Technologies)",

    # 软件工程与系统
    "cs.SE": "软件工程 (Software Engineering)",
    "cs.OS": "操作系统 (Operating Systems)",
    "cs.DC": "分布式计算 (Distributed, Parallel, and Cluster Computing)",
    "cs.DB": "数据库 (Databases)",
    "cs.SY": "系统与控制 (Systems and Control)",
    "cs.PL": "编程语言 (Programming Languages)",
    "cs.SD": "软件开发 (Software Development)",
    "cs.AR": "硬件架构 (Hardware Architecture)",
    "cs.FL": "形式语言与自动机 (Formal Languages and Automata Theory)",
    "cs.PF": "性能 (Performance)",
    

    # 计算机理论学
    "cs.DS": "数据结构与算法 (Data Structures and Algorithms)",
    "cs.CC": "计算复杂度 (Computational Complexity)",
    "cs.CG": "计算几何 (Computational Geometry)",
    "cs.DM": "离散数学 (Discrete Mathematics)",
    "cs.LO": "计算逻辑 (Logic in Computer Science)",
    "cs.GT": "博弈论 (Computer Science and Game Theory)",
    "cs.CR": "密码学与安全 (Cryptography and Security)",
    "cs.NA": "数值分析 (Numerical Analysis)",
    "cs.IT": "信息论 (Information Theory)",
    "cs.NE": "神经与进化计算 (Neural and Evolutionary Computing)", 
    

    # 其他
    "cs.GR": "图形学 (Graphics)",
    "cs.MM": "多媒体 (Multimedia)",
    "cs.SI": "社会与信息网络 (Social and Information Networks)",
    "cs.IR": "信息检索 (Information Retrieval)",
    "cs.NI": "网络与互联网架构 (Networking and Internet Architecture)",
    "cs.BI": "生物信息学 (Bioinformatics)",
    "cs.CB": "计算生物学 (Computational Biology)",
    "cs.GM": "基因组学 (Genomics)",
    "cs.CE": "计算工程、金融与科学 (Computational Engineering, Finance, and Science)",
    
}


def parse_keyword_terms(keywords):
    """解析自由关键词，支持普通空格分词和英文引号短语"""
    normalized = normalize_keywords(keywords)
    if not normalized:
        return []

    terms = []
    for quoted, token in re.findall(r'"([^"]+)"|([^\s,;]+)', normalized):
        term = (quoted or token).strip()
        # 只把用户输入当作普通关键词，不开放 arXiv 字段语法注入。
        term = re.sub(r'["():\[\]{}]', ' ', term)
        term = re.sub(r'\s+', ' ', term).strip()
        if term:
            terms.append(term)
    return terms


def build_arxiv_search_query(categories, keywords=''):
    """构建 arXiv 查询表达式：分类和关键词之间为 AND，关键词之间为 AND"""
    clauses = []

    if categories:
        category_query = ' OR '.join([f"cat:{cat}" for cat in categories])
        clauses.append(f"({category_query})")

    keyword_terms = parse_keyword_terms(keywords)
    if keyword_terms:
        keyword_clauses = []
        for term in keyword_terms:
            if re.search(r'\s|[^A-Za-z0-9_]', term):
                keyword_clauses.append(f'all:"{term}"')
            else:
                keyword_clauses.append(f'all:{term}')
        clauses.append('(' + ' AND '.join(keyword_clauses) + ')')

    if not clauses:
        raise Exception("请至少选择一个 arXiv 分类或输入关键词")

    return ' AND '.join(clauses)


def extract_json_object(content):
    """从模型输出中提取 JSON 对象，兼容被 Markdown 代码块包裹的情况"""
    text = (content or '').strip()
    if text.startswith('```'):
        text = re.sub(r'^```(?:json)?\s*', '', text, flags=re.IGNORECASE)
        text = re.sub(r'\s*```$', '', text)

    start = text.find('{')
    end = text.rfind('}')
    if start == -1 or end == -1 or end <= start:
        return None

    try:
        return json.loads(text[start:end + 1])
    except Exception:
        return None


def strip_reasoning_blocks(text):
    """移除部分推理模型可能返回的思考段落"""
    text = str(text or '').strip()
    text = re.sub(r'<think>[\s\S]*?</think>', '', text, flags=re.IGNORECASE).strip()
    return text


def iter_json_like_blocks(text):
    """按优先级枚举可能包含 JSON 的片段"""
    text = strip_reasoning_blocks(text)
    if not text:
        return

    for match in re.finditer(r'```(?:json|JSON)?\s*([\s\S]*?)```', text):
        block = match.group(1).strip()
        if block:
            yield block

    yield text

    for opener, closer in [('{', '}'), ('[', ']')]:
        start = text.find(opener)
        while start != -1:
            depth = 0
            in_string = False
            escaped = False
            quote_char = ''
            for idx in range(start, len(text)):
                ch = text[idx]
                if in_string:
                    if escaped:
                        escaped = False
                    elif ch == '\\':
                        escaped = True
                    elif ch == quote_char:
                        in_string = False
                    continue

                if ch in ('"', "'"):
                    in_string = True
                    quote_char = ch
                elif ch == opener:
                    depth += 1
                elif ch == closer:
                    depth -= 1
                    if depth == 0:
                        yield text[start:idx + 1].strip()
                        break

            start = text.find(opener, start + 1)


def clean_json_like_text(text):
    """修复常见 LLM JSON 输出小毛刺"""
    cleaned = str(text or '').strip().lstrip('\ufeff')
    cleaned = cleaned.replace('“', '"').replace('”', '"').replace('‘', "'").replace('’', "'")
    cleaned = re.sub(r',\s*([}\]])', r'\1', cleaned)
    cleaned = re.sub(r'\bNone\b', 'null', cleaned)
    cleaned = re.sub(r'\bTrue\b', 'true', cleaned)
    cleaned = re.sub(r'\bFalse\b', 'false', cleaned)
    return cleaned


def parse_json_like(content):
    """解析严格 JSON、代码块 JSON、单引号 dict、尾逗号 JSON 等常见 LLM 输出"""
    for block in iter_json_like_blocks(content):
        cleaned = clean_json_like_text(block)
        if not cleaned:
            continue

        try:
            return json.loads(cleaned)
        except Exception:
            pass

        try:
            parsed = ast.literal_eval(cleaned)
            if isinstance(parsed, (dict, list)):
                return parsed
        except Exception:
            pass

    return None


def collect_text_from_value(value):
    """从 OpenAI 兼容或类 Responses API 字段中递归提取文本"""
    parts = []

    if value is None:
        return parts
    if isinstance(value, str):
        if value.strip():
            parts.append(value)
        return parts
    if isinstance(value, (int, float, bool)):
        return parts
    if isinstance(value, list):
        for item in value:
            parts.extend(collect_text_from_value(item))
        return parts
    if isinstance(value, dict):
        for key in (
            "content", "text", "output_text", "reasoning_content",
            "summary", "message", "data"
        ):
            if key in value:
                parts.extend(collect_text_from_value(value.get(key)))
        return parts

    return parts


def extract_llm_response_text(data):
    """兼容不同 OpenAI-like 响应结构，提取模型最终文本"""
    parts = []

    # OpenAI Responses API 风格
    parts.extend(collect_text_from_value(data.get("output_text")))
    parts.extend(collect_text_from_value(data.get("output")))

    # Chat Completions 风格
    choices = data.get("choices", [])
    if isinstance(choices, list):
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            parts.extend(collect_text_from_value(choice.get("message")))
            parts.extend(collect_text_from_value(choice.get("delta")))
            parts.extend(collect_text_from_value(choice.get("text")))

    text = "\n".join(part.strip() for part in parts if str(part).strip()).strip()
    return text


def describe_llm_response_shape(data):
    """输出安全的响应结构诊断，不打印 API Key 或完整模型内容"""
    if not isinstance(data, dict):
        return f"response_type={type(data).__name__}"

    diagnostics = [f"top_keys={list(data.keys())[:12]}"]
    choices = data.get("choices")
    if isinstance(choices, list):
        diagnostics.append(f"choices_len={len(choices)}")
        if choices and isinstance(choices[0], dict):
            first = choices[0]
            diagnostics.append(f"choice0_keys={list(first.keys())[:12]}")
            if "finish_reason" in first:
                diagnostics.append(f"finish_reason={first.get('finish_reason')}")
            message = first.get("message")
            if isinstance(message, dict):
                diagnostics.append(f"message_keys={list(message.keys())[:12]}")
                content = message.get("content")
                diagnostics.append(f"content_type={type(content).__name__}")
                if isinstance(content, str):
                    diagnostics.append(f"content_len={len(content)}")
                reasoning = message.get("reasoning_content")
                if isinstance(reasoning, str):
                    diagnostics.append(f"reasoning_len={len(reasoning)}")

    output = data.get("output")
    if isinstance(output, list):
        diagnostics.append(f"output_len={len(output)}")

    return "; ".join(diagnostics)


def parse_translation_content(content):
    """优先解析结构化 JSON，失败时回退到旧格式正则，保证兼容已有模型输出"""
    parsed = parse_json_like(content)
    if isinstance(parsed, dict):
        chinese_abstract = str(parsed.get('chineseAbstract') or parsed.get('中文摘要') or '').strip()
        highlight = str(parsed.get('highlight') or parsed.get('亮点') or '').strip()
        if chinese_abstract or highlight:
            return {
                "chineseAbstract": chinese_abstract or "解析失败",
                "highlight": highlight.replace('\n', ' ') or "解析失败",
                "parseMode": "json"
            }

    chinese_abstract_match = re.search(r'中文摘要[:：]\s*([\s\S]*?)(?=\n\s*亮点[:：]|$)', content, re.IGNORECASE)
    highlight_match = re.search(r'亮点[:：]\s*(.+)', content, re.IGNORECASE | re.DOTALL)

    return {
        "chineseAbstract": chinese_abstract_match.group(1).strip() if chinese_abstract_match else "解析失败",
        "highlight": highlight_match.group(1).strip().replace('\n', ' ') if highlight_match else "解析失败",
        "parseMode": "legacy"
    }


def fetch_arxiv_papers(categories, time_range, max_papers=20, keywords=''):
    """从 arXiv 获取论文，带重试机制"""

    # 构建查询
    search_query = build_arxiv_search_query(categories, keywords)
    query_params = {
        "search_query": search_query,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
        "start": "0",
        "max_results": str(max_papers)
    }

    query_string = parse.urlencode(query_params)
    # 使用 HTTPS 更稳定
    url = f"https://export.arxiv.org/api/query?{query_string}"

    # 发送请求，带重试
    max_retries = 3
    xml_data = None
    last_error = None

    for retry in range(max_retries):
        # 延迟避免 429 限流
        if retry > 0:
            import time
            wait_time = 3 * (retry + 1)
            print(f"[fetch] 重试第 {retry + 1}/{max_retries} 次，等待 {wait_time} 秒...")
            time.sleep(wait_time)

        req = request.Request(url, headers={
            "User-Agent": "PaperExpress/1.0 (https://github.com/paperexpress)"
        })

        try:
            with request.urlopen(req, timeout=60) as response:
                xml_data = response.read().decode("utf-8")
                break
        except error.HTTPError as e:
            if e.code == 429:
                last_error = "arXiv API 请求频率过高，请稍后重试 (429 Too Many Requests)"
            else:
                last_error = f"arXiv API 请求错误 ({e.code}): {str(e)}"
        except error.URLError as e:
            last_error = f"arXiv API 连接失败: {str(e)}"
        except Exception as e:
            last_error = f"arXiv API 请求异常: {str(e)}"

    if xml_data is None:
        raise Exception(last_error)

    print(f"[fetch] 成功获取数据，大小: {len(xml_data)} bytes")

    # 解析 XML
    root = ET.fromstring(xml_data)
    entries = root.findall("atom:entry", ARXIV_NS)

    # 计算时间阈值 (使用 UTC 时间保持一致)
    cutoff_date = datetime.utcnow() - timedelta(days=time_range)
    print(f"[fetch] 获取论文，时间范围: {time_range}天，关键词: {normalize_keywords(keywords) or '无'}，阈值: {cutoff_date} UTC")

    papers = []
    for entry in entries:
        published_str = entry.find("atom:published", ARXIV_NS).text
        published = datetime.fromisoformat(published_str.replace("Z", "+00:00")).replace(tzinfo=None)

        # 时间过滤
        if published < cutoff_date:
            continue

        # 提取作者
        authors = []
        for author in entry.findall("atom:author", ARXIV_NS):
            name = author.find("atom:name", ARXIV_NS)
            if name is not None:
                authors.append(name.text)

        # 提取分类
        categories = []
        for cat in entry.findall("atom:category", ARXIV_NS):
            term = cat.get("term")
            if term:
                categories.append(term)

        primary_category_elem = entry.find("arxiv:primary_category", ARXIV_NS)
        primary_category = primary_category_elem.get("term") if primary_category_elem is not None else categories[0] if categories else ""

        # 提取链接
        links = entry.findall("atom:link", ARXIV_NS)
        link = ""
        pdf_link = ""
        for l in links:
            rel = l.get("rel", "")
            href = l.get("href", "")
            title = l.get("title", "")

            if rel == "alternate":
                link = href
            elif rel == "related" and title == "pdf":
                pdf_link = href
            elif title == "pdf":
                pdf_link = href

        paper = {
            "id": entry.find("atom:id", ARXIV_NS).text,
            "title": entry.find("atom:title", ARXIV_NS).text.replace("\n", " ").strip(),
            "abstract": entry.find("atom:summary", ARXIV_NS).text.replace("\n", " ").strip(),
            "authors": authors,
            "published": published_str[:10],
            "updated": entry.find("atom:updated", ARXIV_NS).text[:10],
            "link": link,
            "pdfLink": pdf_link,
            "categories": categories,
            "primaryCategory": primary_category
        }
        papers.append(paper)

    print(f"[fetch] 过滤后共找到 {len(papers)} 篇符合时间范围的论文")
    return papers


def translate_paper(paper, llm_config):
    """使用 LLM 翻译单篇论文，带重试"""

    api_url = llm_config.get("endpoint", "")
    api_key = llm_config.get("key", "")
    model_name = llm_config.get("model", "")

    # 自动补全 /chat/completions 如果没有
    if not api_url.endswith('/chat/completions'):
        api_url = api_url.rstrip('/') + '/chat/completions'

    prompt = f"""请将以下学术论文的摘要翻译成中文，并用一句话总结其核心亮点。

论文标题：{paper['title']}

摘要：{paper['abstract']}

请严格只返回一个 JSON 对象，不要包含 Markdown 代码块或额外解释。格式如下：
{{
  "chineseAbstract": "翻译后的中文摘要",
  "highlight": "一句话亮点"
}}

注意：中文摘要应准确传达原文含义，语言流畅自然。亮点应简洁有力，突出论文的核心创新或价值。"""

    payload = {
        "model": model_name,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7,
        "max_tokens": 2000
    }

    # 重试机制
    max_retries = 2
    last_error = None
    print(f"[translate] 开始翻译: {paper['title'][:50]}...")

    for retry in range(max_retries):
        if retry > 0:
            import time
            print(f"[translate] 重试第 {retry + 1}/{max_retries} 次...")
            time.sleep(2)

        req = request.Request(
            api_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
                "User-Agent": "PaperExpress/1.0"
            },
            method="POST"
        )

        # 创建 SSL 上下文（允许我们自定义证书验证）
        ssl_context = ssl.create_default_context()

        try:
            with request.urlopen(req, context=ssl_context, timeout=180) as response:
                data = json.loads(response.read().decode("utf-8"))
            break
        except error.HTTPError as e:
            error_body = e.read().decode("utf-8")
            try:
                error_json = json.loads(error_body)
                error_msg = error_json.get("error", {}).get("message", str(e))
            except:
                error_msg = error_body or str(e)
            last_error = f"LLM API 错误: {error_msg}"
        except error.URLError as e:
            last_error = f"连接超时，请检查网络或API地址: {str(e)}"
        except Exception as e:
            last_error = f"请求错误: {str(e)}"
    else:
        raise Exception(last_error)

    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")

    # 获取 token 使用信息
    usage = data.get("usage", {})
    prompt_tokens = usage.get("prompt_tokens", 0)
    completion_tokens = usage.get("completion_tokens", 0)
    total_tokens = usage.get("total_tokens", 0)

    # 解析结果：优先 JSON，兼容旧格式
    parsed_content = parse_translation_content(content)

    result = {
        "chineseAbstract": parsed_content["chineseAbstract"],
        "highlight": parsed_content["highlight"],
        "parseMode": parsed_content["parseMode"],
        "promptTokens": prompt_tokens,
        "completionTokens": completion_tokens,
        "totalTokens": total_tokens
    }
    print(f"[translate] 完成: {paper['title'][:30]}... tokens: {total_tokens}")
    return result


def truncate_text(text, max_length):
    """限制传入 LLM 的单字段长度，避免趋势摘要请求过大"""
    text = str(text or '').replace('\n', ' ').strip()
    if len(text) <= max_length:
        return text
    return text[:max_length].rstrip() + '...'


def normalize_trend_items(items, total_papers):
    """清洗 LLM 返回的趋势条目，保证前端收到稳定结构"""
    normalized = []
    if not isinstance(items, list):
        return normalized

    seen = set()
    for item in items:
        if isinstance(item, str):
            item = {"label": item}
        if not isinstance(item, dict):
            continue

        label = str(
            item.get("label") or
            item.get("name") or
            item.get("topic") or
            item.get("method") or
            item.get("term") or
            item.get("关键词") or
            item.get("主题") or
            item.get("方法") or
            ""
        ).strip()
        if not label:
            continue

        label = re.sub(r'^\s*(?:[-*]|\d+[.)、])\s*', '', label)
        label = re.sub(r'^(?:topic|method|主题|方法词|方法)\s*[:：-]\s*', '', label, flags=re.IGNORECASE)
        label = re.sub(r'\s+', ' ', label)[:80]
        label = label.strip(' "\'`，,。；;：:')
        label_key = label.lower()
        label_words = re.findall(r'[a-zA-Z]+', label_key)
        if label_key in BANNED_TREND_LABELS:
            continue
        if len(label_words) == 1 and label_words[0] in BANNED_TREND_LABELS:
            continue
        if len(label_words) == 1 and len(label_words[0]) <= 3 and label != label.upper():
            continue
        if not re.search(r'[A-Za-z\u4e00-\u9fff]', label):
            continue

        if label_key in seen:
            continue
        seen.add(label_key)

        try:
            count_raw = item.get("count", item.get("paperCount", item.get("覆盖论文数", 1)))
            count_match = re.search(r'\d+', str(count_raw))
            count = int(count_match.group(0)) if count_match else 1
        except Exception:
            count = 1
        count = max(1, min(total_papers, count))

        reason = truncate_text(item.get("reason") or item.get("原因") or item.get("description") or "", 120)
        normalized.append({
            "label": label,
            "count": count,
            "reason": reason
        })

    normalized.sort(key=lambda x: (-x["count"], x["label"].lower()))
    return normalized[:8]


def first_present(mapping, keys):
    """从 dict 中按多个候选键取第一个存在值，大小写不敏感"""
    if not isinstance(mapping, dict):
        return None

    lowered = {str(k).lower(): v for k, v in mapping.items()}
    for key in keys:
        if key in mapping:
            return mapping[key]
        lower_key = str(key).lower()
        if lower_key in lowered:
            return lowered[lower_key]
    return None


def split_typed_trend_items(items):
    """兼容 [{type: topic/method, ...}] 结构"""
    topics = []
    methods = []
    if not isinstance(items, list):
        return topics, methods

    for item in items:
        if not isinstance(item, dict):
            continue
        item_type = str(
            item.get("type") or item.get("category") or item.get("kind") or
            item.get("类别") or item.get("类型") or ""
        ).lower()
        if "method" in item_type or "方法" in item_type or "技术" in item_type:
            methods.append(item)
        elif "topic" in item_type or "主题" in item_type or "方向" in item_type:
            topics.append(item)

    return topics, methods


def parse_markdown_trend_sections(content):
    """当模型没有返回 JSON 时，从 Markdown/纯文本小节中尽量提取条目"""
    topics = []
    methods = []
    current = None
    text = strip_reasoning_blocks(content)

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        lower = line.lower()
        if re.search(r'\btopics?\b|主题|研究方向|问题域', lower):
            current = "topics"
            continue
        if re.search(r'\bmethods?\b|方法词|技术路线|算法|模型结构', lower):
            current = "methods"
            continue

        item_match = re.match(r'^(?:[-*]|\d+[.)、])\s*(.+)$', line)
        if not item_match or current not in ("topics", "methods"):
            continue

        item_text = item_match.group(1).strip()
        label = re.split(r'\s*(?:[:：\-—]|，|。)\s*', item_text, maxsplit=1)[0].strip()
        if not label:
            continue
        target = topics if current == "topics" else methods
        target.append({"label": label, "count": 1, "reason": ""})

    return {"topics": topics, "methods": methods}


def parse_trend_summary_content(content):
    """解析趋势摘要，兼容标准 JSON、中文键、typed items 和 Markdown 兜底"""
    parsed = parse_json_like(content)

    if isinstance(parsed, dict):
        topics = first_present(parsed, [
            "topics", "topic", "Topics", "Topic", "热门Topic", "主题", "研究主题", "研究方向", "趋势主题"
        ])
        methods = first_present(parsed, [
            "methods", "method", "Methods", "Method", "methodWords", "方法词", "方法", "技术方法", "技术路线"
        ])

        typed_topics, typed_methods = split_typed_trend_items(
            first_present(parsed, ["items", "trends", "results", "结果", "趋势"]) or []
        )
        if topics is None and typed_topics:
            topics = typed_topics
        if methods is None and typed_methods:
            methods = typed_methods

        return {
            "topics": topics if isinstance(topics, list) else [],
            "methods": methods if isinstance(methods, list) else []
        }

    if isinstance(parsed, list):
        typed_topics, typed_methods = split_typed_trend_items(parsed)
        if typed_topics or typed_methods:
            return {"topics": typed_topics, "methods": typed_methods}
        return {"topics": parsed, "methods": []}

    return parse_markdown_trend_sections(content)


def post_llm_json_request(api_url, api_key, payload, timeout=180, allow_response_format_fallback=False):
    """发送 LLM 请求；当 response_format 不兼容时自动降级重试"""

    def do_post(request_payload):
        req = request.Request(
            api_url,
            data=json.dumps(request_payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
                "User-Agent": "PaperExpress/1.0"
            },
            method="POST"
        )

        ssl_context = ssl.create_default_context()
        with request.urlopen(req, context=ssl_context, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    try:
        return do_post(payload)
    except error.HTTPError as e:
        error_body = e.read().decode("utf-8")
        try:
            error_json = json.loads(error_body)
            error_msg = error_json.get("error", {}).get("message", str(e))
        except Exception:
            error_msg = error_body or str(e)

        if allow_response_format_fallback and "response_format" in payload:
            fallback_payload = dict(payload)
            fallback_payload.pop("response_format", None)
            try:
                safe_print(f"[trend_summary] response_format 不兼容，已降级普通 JSON 提示: {truncate_text(error_msg, 120)}")
                return do_post(fallback_payload)
            except error.HTTPError as fallback_error:
                fallback_body = fallback_error.read().decode("utf-8")
                try:
                    fallback_json = json.loads(fallback_body)
                    fallback_msg = fallback_json.get("error", {}).get("message", str(fallback_error))
                except Exception:
                    fallback_msg = fallback_body or str(fallback_error)
                raise Exception(f"LLM API 错误: {fallback_msg}")

        raise Exception(f"LLM API 错误: {error_msg}")
    except error.URLError as e:
        raise Exception(f"连接超时，请检查网络或API地址: {str(e)}")


def summarize_trends_with_llm(papers, llm_config):
    """调用一次 LLM，为当前结果集生成 Topic 和方法词"""

    api_url = llm_config.get("endpoint", "")
    api_key = llm_config.get("key", "")
    model_name = llm_config.get("model", "")

    if not api_url or not model_name:
        raise Exception("LLM API 地址和模型名称不能为空")

    if not api_url.endswith('/chat/completions'):
        api_url = api_url.rstrip('/') + '/chat/completions'

    compact_papers = []
    for idx, paper in enumerate(papers[:30], start=1):
        compact_papers.append({
            "index": idx,
            "title": truncate_text(paper.get("title", ""), 160),
            "abstract": truncate_text(paper.get("abstract", ""), 420),
            "chineseAbstract": truncate_text(paper.get("chineseAbstract", ""), 220),
            "highlight": truncate_text(paper.get("highlight", ""), 140),
            "primaryCategory": paper.get("primaryCategory", ""),
            "published": paper.get("published", "")
        })

    system_prompt = """你是一名论文情报分析师。你必须只输出一个可被 json.loads 解析的 JSON 对象。不要输出 Markdown，不要输出代码块，不要输出解释性文字。JSON 顶层只能包含 topics 和 methods 两个数组。"""

    prompt = f"""请基于下面这批 arXiv 论文的标题、摘要、分类和亮点，生成当前结果集的趋势摘要。

任务：
1. 生成 topics：当前论文集中真正有研究意义的 Topic。Topic 应是研究方向、任务、应用场景或问题域，例如 "LLM serving", "robot manipulation", "retrieval-augmented generation"。不要做词频统计，不要输出 these、can、not、are、execution、present、access、control、design、existing、model、method、data、learning 等泛词或功能词。
2. 生成 methods：当前论文集中出现的方法词、技术路线、模型结构或算法组件，例如 "KV cache", "diffusion policy", "LoRA", "graph neural network"。不要把普通形容词、动词、停用词、数据集名、评价指标或 Benchmark 当作方法。
3. 合并同义表达，使用简洁标签；英文技术词保留英文，必要时可用中文。
4. count 表示该条目大致覆盖了当前 {len(compact_papers)} 篇论文中的多少篇。请按 count 从高到低排序。
5. reason 用一句中文说明该条目为何重要，必须简短。
6. label 优先使用 2-5 个词的短语；除 RAG、LoRA、MoE、LLM 等公认缩写外，不要输出单个普通英文词。
7. 如果没有足够证据形成高质量 Topic 或方法词，请返回空数组，不要用高频词凑数。

请严格只返回 JSON，不要 Markdown，不要解释，不要使用单引号。格式：
{{
  "topics": [
    {{"label": "Topic 名称", "count": 3, "reason": "简短原因"}}
  ],
  "methods": [
    {{"label": "方法词名称", "count": 2, "reason": "简短原因"}}
  ]
}}

论文列表：
{json.dumps(compact_papers, ensure_ascii=False)}
"""

    payload = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.2,
        "max_tokens": 1600,
        "stream": False,
        "response_format": {"type": "json_object"}
    }

    data = post_llm_json_request(api_url, api_key, payload, timeout=180, allow_response_format_fallback=True)

    usage = data.get("usage", {})
    total_papers = max(1, len(compact_papers))
    content = extract_llm_response_text(data)
    if not content:
        safe_print(f"[trend_summary] 模型响应文本为空，响应结构: {describe_llm_response_shape(data)}")

    parsed = parse_trend_summary_content(content)
    topics = normalize_trend_items(parsed.get("topics", []), total_papers)
    methods = normalize_trend_items(parsed.get("methods", []), total_papers)

    warning = ""
    if not topics and not methods:
        warning = "趋势摘要未提取到可展示条目"
        preview = truncate_text(content, 300)
        safe_print(f"[trend_summary] 未提取到趋势条目，模型输出预览: {preview}")

    return {
        "topics": topics,
        "methods": methods,
        "warning": warning,
        "promptTokens": usage.get("prompt_tokens", 0),
        "completionTokens": usage.get("completion_tokens", 0),
        "totalTokens": usage.get("total_tokens", 0)
    }


def intensive_read_paper(paper, llm_config):
    """使用 LLM 对论文进行精读分析"""

    api_url = llm_config.get("endpoint", "")
    api_key = llm_config.get("key", "")
    model_name = llm_config.get("model", "")

    # 自动补全 /chat/completions 如果没有
    if not api_url.endswith('/chat/completions'):
        api_url = api_url.rstrip('/') + '/chat/completions'

    # 读取精读提示词模板
    prompt_template_path = os.path.join(os.path.dirname(__file__), 'Prompts', 'intensive_reading_prompt.txt')

    try:
        with open(prompt_template_path, 'r', encoding='utf-8') as f:
            prompt_template = f.read()
    except Exception as e:
        raise Exception(f"无法读取精读提示词模板: {str(e)}")

    # 构建论文内容
    authors_str = ', '.join(paper.get('authors', []))
    categories_str = ', '.join(paper.get('categories', []))

    paper_content = f"""论文标题: {paper.get('title', '')}

作者: {authors_str}

arXiv ID: {paper.get('id', '')}

发布日期: {paper.get('published', '')}

分类: {categories_str}

摘要:
{paper.get('abstract', '')}"""

    # 使用模板生成提示词
    prompt = f"""{prompt_template}

======================
请开始分析以下论文：
======================

{paper_content}"""

    payload = {
        "model": model_name,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7,
        "max_tokens": 8000
    }

    # 重试机制
    max_retries = 2
    last_error = None
    print(f"[intensive_read] 开始精读: {paper.get('title', '')[:50]}...")

    for retry in range(max_retries):
        if retry > 0:
            import time
            print(f"[intensive_read] 重试第 {retry + 1}/{max_retries} 次...")
            time.sleep(2)

        req = request.Request(
            api_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
                "User-Agent": "PaperExpress/1.0"
            },
            method="POST"
        )

        ssl_context = ssl.create_default_context()

        try:
            with request.urlopen(req, context=ssl_context, timeout=300) as response:
                data = json.loads(response.read().decode("utf-8"))
            break
        except error.HTTPError as e:
            error_body = e.read().decode("utf-8")
            try:
                error_json = json.loads(error_body)
                error_msg = error_json.get("error", {}).get("message", str(e))
            except:
                error_msg = error_body or str(e)
            last_error = f"LLM API 错误: {error_msg}"
        except error.URLError as e:
            last_error = f"连接超时，请检查网络或API地址: {str(e)}"
        except Exception as e:
            last_error = f"请求错误: {str(e)}"
    else:
        raise Exception(last_error)

    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")

    # 获取 token 使用信息
    usage = data.get("usage", {})
    prompt_tokens = usage.get("prompt_tokens", 0)
    completion_tokens = usage.get("completion_tokens", 0)
    total_tokens = usage.get("total_tokens", 0)

    result = {
        "content": content,
        "promptTokens": prompt_tokens,
        "completionTokens": completion_tokens,
        "totalTokens": total_tokens
    }
    print(f"[intensive_read] 完成: {paper.get('title', '')[:30]}... tokens: {total_tokens}")
    return result


def test_llm_connection(llm_config):
    """测试 LLM 连接"""

    api_url = llm_config.get("endpoint", "")
    api_key = llm_config.get("key", "")
    model_name = llm_config.get("model", "")

    if not api_url or not model_name:
        return {"success": False, "message": "API 地址和模型名称不能为空"}

    # 自动补全 /chat/completions 如果没有
    if not api_url.endswith('/chat/completions'):
        api_url = api_url.rstrip('/') + '/chat/completions'

    payload = {
        "model": model_name,
        "messages": [{"role": "user", "content": "Hello, this is a test. Please respond with 'OK' only."}],
        "max_tokens": 10
    }

    req = request.Request(
        api_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "PaperExpress/1.0"
        },
        method="POST"
    )

    ssl_context = ssl.create_default_context()

    try:
        with request.urlopen(req, context=ssl_context, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
            if data.get("choices"):
                return {"success": True, "message": "连接成功"}
            else:
                return {"success": False, "message": "API 返回异常，请检查模型名称是否正确"}
    except error.HTTPError as e:
        error_body = e.read().decode("utf-8")
        try:
            error_json = json.loads(error_body)
            error_msg = error_json.get("error", {}).get("message", str(e))
        except:
            error_msg = error_body or str(e)
        return {"success": False, "message": f"HTTP {e.code}: {error_msg}"}
    except error.URLError as e:
        return {"success": False, "message": f"连接失败: {str(e)}"}
    except Exception as e:
        return {"success": False, "message": f"错误: {str(e)}"}


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """支持多线程的 HTTP 服务器"""
    daemon_threads = True
    allow_reuse_address = True


class PaperExpressHandler(BaseHTTPRequestHandler):
    """HTTP 请求处理器"""

    # MIME types
    MIME_TYPES = {
        '.html': 'text/html',
        '.css': 'text/css',
        '.js': 'application/javascript',
        '.json': 'application/json',
        '.png': 'image/png',
        '.jpg': 'image/jpeg',
        '.gif': 'image/gif',
        '.svg': 'image/svg+xml',
        '.ico': 'image/x-icon'
    }

    def log_message(self, format, *args):
        """自定义日志"""
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {format % args}")

    def guess_mime_type(self, path):
        """猜测 MIME type"""
        ext = os.path.splitext(path)[1].lower()
        return self.MIME_TYPES.get(ext, 'application/octet-stream')

    def serve_static_file(self, path):
        """提供静态文件服务"""
        # 前端文件在 Frontend 文件夹
        base_dir = os.path.join(os.path.dirname(__file__), 'Frontend')
        parsed_path = urlparse(path).path

        if parsed_path == '/':
            parsed_path = '/index.html'

        file_path = os.path.normpath(os.path.join(base_dir, parsed_path.lstrip('/')))

        # 安全检查：防止路径遍历
        if not file_path.startswith(os.path.normpath(base_dir)):
            self._set_headers(403, 'text/plain')
            self.wfile.write(b'Forbidden')
            return

        if not os.path.exists(file_path) or not os.path.isfile(file_path):
            return False

        try:
            with open(file_path, 'rb') as f:
                content = f.read()
            mime_type = self.guess_mime_type(file_path)
            self._set_headers(200, mime_type)
            self.wfile.write(content)
            return True
        except Exception as e:
            return False

    def _set_headers(self, status_code=200, content_type="application/json"):
        """设置响应头"""
        self.send_response(status_code)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.send_header("X-PaperExpress-Version", STATIC_ASSET_VERSION)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_OPTIONS(self):
        """处理 CORS 预检请求"""
        self._set_headers()

    def do_GET(self):
        """处理 GET 请求"""
        path = self.path

        # API 请求
        if path == "/api/categories":
            self._set_headers()
            response = {"success": True, "categories": ARXIV_CATEGORIES}
            self.wfile.write(json.dumps(response, ensure_ascii=False).encode("utf-8"))
        elif path == "/api/config":
            response = self._handle_load_config()
            self._set_headers()
            self.wfile.write(json.dumps(response, ensure_ascii=False).encode("utf-8"))
        elif path == "/api/history/list":
            response = self._handle_history_list()
            self._set_headers()
            self.wfile.write(json.dumps(response, ensure_ascii=False).encode("utf-8"))
        elif path.startswith("/api/history/papers/"):
            hash_key = path.split("/")[-1]
            response = self._handle_history_papers_get(hash_key)
            self._set_headers()
            self.wfile.write(json.dumps(response, ensure_ascii=False).encode("utf-8"))
        elif path.startswith("/api/history/intensive/"):
            hash_key = path.split("/")[-1]
            response = self._handle_history_intensive_get(hash_key)
            self._set_headers()
            self.wfile.write(json.dumps(response, ensure_ascii=False).encode("utf-8"))
        else:
            # 尝试提供静态文件
            if not self.serve_static_file(self.path):
                self._set_headers(404, "application/json")
                self.wfile.write(json.dumps({"error": "Not found"}).encode())

    def do_DELETE(self):
        """处理 DELETE 请求"""
        path = self.path
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"

        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            self._set_headers(400)
            self.wfile.write(json.dumps({"error": "Invalid JSON"}).encode())
            return

        try:
            if path.startswith("/api/history/papers/"):
                hash_key = path.split("/")[-1]
                response = self._handle_history_papers_delete(hash_key)
                self._set_headers()
                self.wfile.write(json.dumps(response, ensure_ascii=False).encode("utf-8"))
            elif path.startswith("/api/history/intensive/"):
                hash_key = path.split("/")[-1]
                response = self._handle_history_intensive_delete(hash_key)
                self._set_headers()
                self.wfile.write(json.dumps(response, ensure_ascii=False).encode("utf-8"))
            else:
                self._set_headers(404)
                self.wfile.write(json.dumps({"error": "Not found"}).encode())
        except Exception as e:
            self._set_headers(500)
            self.wfile.write(json.dumps({"error": str(e)}).encode())

    def do_POST(self):
        """处理 POST 请求"""
        path = self.path

        # 读取请求体
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8")

        try:
            data = json.loads(body) if body else {}
        except json.JSONDecodeError:
            self._set_headers(400)
            self.wfile.write(json.dumps({"error": "Invalid JSON"}).encode())
            return

        try:
            if path == "/api/fetch":
                response = self._handle_fetch(data)
            elif path == "/api/translate":
                response = self._handle_translate(data)
            elif path == "/api/translate_batch":
                response = self._handle_translate_batch(data)
            elif path == "/api/test":
                response = self._handle_test(data)
            elif path == "/api/intensive_read":
                response = self._handle_intensive_read(data)
            elif path == "/api/trend_summary":
                response = self._handle_trend_summary(data)
            elif path == "/api/history/check":
                response = self._handle_history_check(data)
            else:
                self._set_headers(404)
                self.wfile.write(json.dumps({"error": "Not found"}).encode())
                return

            self._set_headers()
            self.wfile.write(json.dumps(response, ensure_ascii=False).encode("utf-8"))

        except Exception as e:
            self._set_headers(500)
            self.wfile.write(json.dumps({"error": str(e)}).encode())

    def _handle_fetch(self, data):
        """处理获取论文请求"""
        categories = data.get("categories", [])
        time_range = data.get("timeRange", 3)
        max_papers = data.get("maxPapers", 20)
        keywords = normalize_keywords(data.get("keywords", ""))

        papers = fetch_arxiv_papers(categories, time_range, max_papers, keywords)

        return {
            "success": True,
            "papers": papers,
            "count": len(papers)
        }

    def _handle_translate(self, data):
        """处理翻译请求"""
        paper = data.get("paper", {})
        llm_config = data.get("llm", {})

        result = translate_paper(paper, llm_config)

        return {
            "success": True,
            "result": result
        }

    def _handle_translate_batch(self, data):
        """批量并发翻译处理"""
        papers = data.get("papers", [])
        llm_config = data.get("llm", {})
        concurrency = max(1, min(10, data.get("concurrency", 3)))  # 限制 1-10 并发
        categories = data.get("categories", [])
        time_range = data.get("timeRange", 3)
        max_papers = data.get("maxPapers", 20)
        keywords = normalize_keywords(data.get("keywords", ""))
        model_name = llm_config.get("model", "")
        paper_ids = [paper.get("id", "") for paper in papers if paper.get("id")]

        # 检查缓存
        cache_hash = paper_config_hash(
            categories,
            time_range,
            max_papers,
            keywords,
            model_name,
            TRANSLATION_PROMPT_VERSION,
            paper_ids
        )
        cached = get_papers_record(cache_hash)
        if cached:
            print(f"[batch] 命中缓存: {cache_hash}, {len(papers)} 篇论文")
            return {
                "success": True,
                "results": cached.get("results", []),
                "stats": cached.get("stats", {}),
                "papers": cached.get("papers", []),
                "cached": True,
                "cacheCreated": cached.get("created", "")
            }

        print(f"[batch] 开始批量翻译 {len(papers)} 篇论文，并发数: {concurrency}")

        result_queue = queue.Queue()
        paper_queue = queue.Queue()

        # 将论文放入队列，附带原始索引
        for idx, paper in enumerate(papers):
            paper_queue.put((idx, paper))

        start_time = time.time()

        def worker():
            while True:
                try:
                    idx, paper = paper_queue.get(block=False)
                except queue.Empty:
                    break

                try:
                    result = translate_paper(paper, llm_config)
                    result_queue.put((idx, {
                        "success": True,
                        "result": result
                    }))
                except Exception as e:
                    result_queue.put((idx, {
                        "success": False,
                        "error": str(e),
                        "result": {
                            "chineseAbstract": f"翻译失败: {str(e)}",
                            "highlight": "无法生成亮点",
                            "promptTokens": 0,
                            "completionTokens": 0,
                            "totalTokens": 0
                        }
                    }))

                paper_queue.task_done()

        # 启动工作线程
        threads = []
        for _ in range(concurrency):
            t = threading.Thread(target=worker)
            t.start()
            threads.append(t)

        # 等待所有线程完成
        for t in threads:
            t.join()

        # 按原始顺序整理结果
        results = [None] * len(papers)
        while not result_queue.empty():
            idx, result = result_queue.get()
            results[idx] = result

        total_time = time.time() - start_time

        # 统计 token 使用
        total_prompt_tokens = sum(r["result"]["promptTokens"] for r in results if r and r["result"])
        total_completion_tokens = sum(r["result"]["completionTokens"] for r in results if r and r["result"])
        total_tokens = sum(r["result"]["totalTokens"] for r in results if r and r["result"])

        print(f"[batch] 完成! 总耗时: {total_time:.1f}s, tokens: {total_tokens}")

        stats = {
            "totalPapers": len(papers),
            "totalTime": round(total_time, 2),
            "avgTimePerPaper": round(total_time / len(papers), 2) if papers else 0,
            "promptTokens": total_prompt_tokens,
            "completionTokens": total_completion_tokens,
            "totalTokens": total_tokens
        }

        # 保存到缓存
        cat_names = ','.join(sorted(categories))
        title_parts = []
        if cat_names:
            title_parts.append(cat_names)
        if keywords:
            title_parts.append(f"关键词: {keywords}")
        title_suffix = ' / '.join(title_parts) if title_parts else '全部'
        cache_record = {
            "schemaVersion": CACHE_SCHEMA_VERSION,
            "promptVersion": TRANSLATION_PROMPT_VERSION,
            "title": f"论文速递: {title_suffix}",
            "categories": categories,
            "keywords": keywords,
            "timeRange": time_range,
            "maxPapers": max_papers,
            "count": len(papers),
            "model": model_name,
            "papers": papers,
            "results": results,
            "stats": stats,
            "created": datetime.now().isoformat()
        }
        save_papers_record(cache_hash, cache_record)
        print(f"[batch] 已保存缓存: {cache_hash}")

        return {
            "success": True,
            "results": results,
            "stats": stats,
            "papers": papers,
            "cached": False
        }

    def _handle_test(self, data):
        """处理连接测试请求"""
        llm_config = data.get("llm", {})

        result = test_llm_connection(llm_config)

        return result

    def _handle_trend_summary(self, data):
        """处理当前结果集趋势摘要请求"""
        papers = data.get("papers", [])
        llm_config = data.get("llm", {})

        if not papers:
            return {"success": False, "error": "论文数据不能为空"}

        result = summarize_trends_with_llm(papers, llm_config)

        return {
            "success": True,
            "result": result
        }

    def _handle_intensive_read(self, data):
        """处理论文精读请求"""
        paper = data.get("paper", {})
        llm_config = data.get("llm", {})
        save_cache = data.get("saveCache", True)  # 是否保存缓存

        if not paper:
            return {"success": False, "error": "论文数据不能为空"}

        arXiv_id = paper.get("id", "")
        paper_title = paper.get("title", "")

        # 先检查缓存
        if save_cache:
            cache_hash = intensive_hash(arXiv_id, paper_title)
            cached = get_intensive_record(cache_hash)
            if cached:
                print(f"[intensive_read] 命中缓存: {paper_title[:30]}...")
                return {
                    "success": True,
                    "result": cached.get("result", {}),
                    "cached": True,
                    "cacheCreated": cached.get("created", "")
                }

        result = intensive_read_paper(paper, llm_config)

        # 保存缓存
        if save_cache:
            cache_record = {
                "paperTitle": paper_title,
                "arXivId": arXiv_id,
                "paper": paper,
                "result": result,
                "created": datetime.now().isoformat()
            }
            save_intensive_record(cache_hash, cache_record)
            print(f"[intensive_read] 已保存缓存: {paper_title[:30]}...")

        return {
            "success": True,
            "result": result,
            "cached": False
        }

    def _handle_history_check(self, data):
        """检查历史缓存是否存在"""
        check_type = data.get("type", "")  # "papers" or "intensive"

        if check_type == "papers":
            categories = data.get("categories", [])
            time_range = data.get("timeRange", 3)
            max_papers = data.get("maxPapers", 20)
            keywords = normalize_keywords(data.get("keywords", ""))
            model_name = data.get("model", "")
            paper_ids = data.get("paperIds", [])

            if not paper_ids:
                index = load_index()
                target_categories = sorted(categories)
                for key in index.get("papers", {}):
                    record = get_papers_record(key)
                    if not record:
                        continue
                    same_config = (
                        sorted(record.get("categories", [])) == target_categories and
                        record.get("timeRange", 3) == time_range and
                        record.get("maxPapers", 20) == max_papers and
                        normalize_keywords(record.get("keywords", "")) == keywords and
                        (not model_name or record.get("model", "") == model_name)
                    )
                    if same_config:
                        return {
                            "success": True,
                            "cached": True,
                            "hash": key,
                            "title": record.get("title", ""),
                            "created": record.get("created", ""),
                            "count": record.get("count", len(record.get("papers", []))),
                            "keywords": record.get("keywords", "")
                        }

            cache_hash = paper_config_hash(categories, time_range, max_papers, keywords, model_name, paper_ids=paper_ids)
            cached = get_papers_record(cache_hash)
            if cached:
                return {
                    "success": True,
                    "cached": True,
                    "hash": cache_hash,
                    "title": cached.get("title", ""),
                    "created": cached.get("created", ""),
                    "count": cached.get("count", len(cached.get("papers", []))),
                    "keywords": cached.get("keywords", "")
                }
            return {"success": True, "cached": False, "hash": cache_hash}

        elif check_type == "intensive":
            arXiv_id = data.get("arXivId", "")
            paper_title = data.get("paperTitle", "")
            cache_hash = intensive_hash(arXiv_id, paper_title)
            cached = get_intensive_record(cache_hash)
            if cached:
                return {
                    "success": True,
                    "cached": True,
                    "hash": cache_hash,
                    "paperTitle": cached.get("paperTitle", ""),
                    "created": cached.get("created", "")
                }
            return {"success": True, "cached": False, "hash": cache_hash}

        return {"success": False, "error": "未知的检查类型"}

    def _handle_history_list(self):
        """获取所有历史记录列表"""
        index = load_index()
        for key, meta in index.get("papers", {}).items():
            record = get_papers_record(key)
            if record:
                paper_count = record.get("count")
                if paper_count is None or paper_count == 0:
                    paper_count = len(record.get("papers", []))
                meta["count"] = paper_count
                meta["keywords"] = record.get("keywords", meta.get("keywords", ""))
        return {
            "success": True,
            "papers": index.get("papers", {}),
            "intensive": index.get("intensive", {})
        }

    def _handle_history_papers_get(self, hash_key):
        """获取指定论文速递记录"""
        record = get_papers_record(hash_key)
        if record:
            return {"success": True, "record": record}
        return {"success": False, "error": "记录不存在"}

    def _handle_history_papers_delete(self, hash_key):
        """删除论文速递记录"""
        delete_papers_record(hash_key)
        return {"success": True}

    def _handle_history_intensive_get(self, hash_key):
        """获取指定精读记录"""
        record = get_intensive_record(hash_key)
        if record:
            return {"success": True, "record": record}
        return {"success": False, "error": "记录不存在"}

    def _handle_history_intensive_delete(self, hash_key):
        """删除精读记录"""
        delete_intensive_record(hash_key)
        return {"success": True}

    def _handle_load_config(self):
        """加载配置文件"""
        config_path = os.path.join(os.path.dirname(__file__), 'config.json')

        if not os.path.exists(config_path):
            return {
                "success": False,
                "message": "根目录未找到 config.json 配置文件"
            }

        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)

            return {
                "success": True,
                "config": {
                    "endpoint": config.get("endpoint", ""),
                    "key": config.get("key", ""),
                    "model": config.get("model", "")
                }
            }
        except json.JSONDecodeError as e:
            return {
                "success": False,
                "message": f"配置文件 JSON 格式错误: {str(e)}"
            }
        except Exception as e:
            return {
                "success": False,
                "message": f"读取配置文件失败: {str(e)}"
            }


def run_server(port=8080):
    """启动服务器"""
    server_address = ("", port)
    httpd = ThreadedHTTPServer(server_address, PaperExpressHandler)

    safe_print(f"""
╔══════════════════════════════════════════════════════════╗
║                    PaperExpress Server                   ║
║                                                          ║
║  🚀 服务已启动: http://localhost:{port}                    ║
║                                                          ║
║  API 端点:                                                ║
║    - GET  /api/categories       获取学科分类列表          ║
║    - GET  /api/config          加载配置文件              ║
║    - POST /api/fetch          获取 arXiv 论文            ║
║    - POST /api/translate      翻译单篇论文               ║
║    - POST /api/translate_batch 批量翻译(支持并发)        ║
║    - POST /api/intensive_read  论文精读分析              ║
║    - POST /api/trend_summary   LLM 趋势摘要              ║
║    - POST /api/test           测试 LLM 连接              ║
║                                                          ║
║  在浏览器打开上述地址即可使用                              ║
║  按 Ctrl+C 停止服务                                       ║
╚══════════════════════════════════════════════════════════╝
    """)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n\n服务已停止")
        sys.exit(0)


if __name__ == "__main__":
    port = 8080
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except ValueError:
            print("端口必须是整数")
            sys.exit(1)

    run_server(port)
