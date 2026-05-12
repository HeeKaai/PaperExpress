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


def compute_hash(*parts):
    """计算配置的哈希值（用于缓存键）"""
    combined = '|'.join(str(p) for p in parts)
    return hashlib.md5(combined.encode('utf-8')).hexdigest()[:16]


def paper_config_hash(categories, time_range, max_papers):
    """论文速递配置的哈希值"""
    cats = ','.join(sorted(categories))
    return compute_hash(cats, time_range, max_papers)


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

    # 更新索引
    index = load_index()
    index['papers'][hash_key] = {
        "title": record.get('title', '未命名'),
        "created": record.get('created', datetime.now().isoformat()),
        "count": record.get('count', 0)
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


def fetch_arxiv_papers(categories, time_range, max_papers=20):
    """从 arXiv 获取论文，带重试机制"""

    # 构建查询
    cat_query = "+OR+".join([f"cat:{cat}" for cat in categories])
    query_params = {
        "search_query": cat_query,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
        "start": "0",
        "max_results": str(max_papers)
    }

    query_string = "&".join([f"{k}={v}" for k, v in query_params.items()])
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
    print(f"[fetch] 获取论文，时间范围: {time_range}天，阈值: {cutoff_date} UTC")

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

请按以下格式返回：
中文摘要：[翻译后的中文摘要]
亮点：[一句话亮点]

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

    # 解析结果
    chinese_abstract_match = re.search(r'中文摘要[:：]\s*([\s\S]*?)(?=\n\s*亮点[:：]|$)', content, re.IGNORECASE)
    highlight_match = re.search(r'亮点[:：]\s*(.+)', content, re.IGNORECASE | re.DOTALL)

    result = {
        "chineseAbstract": chinese_abstract_match.group(1).strip() if chinese_abstract_match else "解析失败",
        "highlight": highlight_match.group(1).strip().replace('\n', ' ') if highlight_match else "解析失败",
        "promptTokens": prompt_tokens,
        "completionTokens": completion_tokens,
        "totalTokens": total_tokens
    }
    print(f"[translate] 完成: {paper['title'][:30]}... tokens: {total_tokens}")
    return result


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

        if path == '/':
            path = '/index.html'

        file_path = os.path.normpath(os.path.join(base_dir, path.lstrip('/')))

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

        papers = fetch_arxiv_papers(categories, time_range, max_papers)

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
        model_name = llm_config.get("model", "")

        # 检查缓存
        cache_hash = paper_config_hash(categories, time_range, max_papers)
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
        cache_record = {
            "title": f"论文速递: {cat_names}",
            "categories": categories,
            "timeRange": time_range,
            "maxPapers": max_papers,
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
            cache_hash = paper_config_hash(categories, time_range, max_papers)
            cached = get_papers_record(cache_hash)
            if cached:
                return {
                    "success": True,
                    "cached": True,
                    "hash": cache_hash,
                    "title": cached.get("title", ""),
                    "created": cached.get("created", ""),
                    "count": cached.get("count", 0)
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

    print(f"""
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
