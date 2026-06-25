"""Research-question agent for PaperExpress.

This module stays independent from server.py. The server passes in the existing
arXiv fetcher and LLM helper functions so the original paper-express pipeline
can remain unchanged.
"""

import json
import re
import time
from datetime import datetime

from .tooling import AgentTool, AgentTrajectory, ToolRegistry

AGENT_PROMPT_VERSION = "research-agent-runtime-v3"
DEFAULT_TIME_RANGE = 180
DEFAULT_MAX_PAPERS = 25
DEFAULT_PAPERS_PER_QUERY = 15
DEFAULT_MAX_SUBQUERIES = 5
TOP_PAPERS_FOR_SYNTHESIS = 25

DEFAULT_AGENT_CATEGORIES = ["cs.SE", "cs.AI", "cs.LG", "cs.CL"]
ALLOWED_AGENT_CATEGORIES = {
    "cs.AI", "cs.CL", "cs.CV", "cs.LG", "cs.RO", "cs.CY", "cs.HC",
    "cs.MA", "cs.SC", "cs.ET", "cs.SE", "cs.OS", "cs.DC", "cs.DB",
    "cs.SY", "cs.PL", "cs.SD", "cs.AR", "cs.FL", "cs.PF", "cs.DS",
    "cs.CC", "cs.CG", "cs.DM", "cs.LO", "cs.GT", "cs.CR", "cs.NA",
    "cs.IT", "cs.NE", "cs.GR", "cs.MM", "cs.SI", "cs.IR", "cs.NI",
    "cs.BI", "cs.CB", "cs.GM", "cs.CE"
}


def normalize_question(question):
    """Normalize a free-form research question for cache keys and prompts."""
    return re.sub(r"\s+", " ", str(question or "").strip())


def clamp_int(value, default, min_value, max_value):
    """Parse and clamp an integer UI/API parameter."""
    try:
        parsed = int(value)
    except Exception:
        parsed = default
    return max(min_value, min(max_value, parsed))


def normalize_api_url(endpoint):
    """Normalize an OpenAI-compatible endpoint to /chat/completions."""
    api_url = str(endpoint or "").strip()
    if api_url and not api_url.endswith("/chat/completions"):
        api_url = api_url.rstrip("/") + "/chat/completions"
    return api_url


def build_source_priorities():
    """Return the v1 source priority plan."""
    return [
        {
            "name": "arXiv",
            "priority": 1,
            "status": "executed",
            "reason": "计算机科学预印本更新快，适合作为最新进展的主检索源。"
        },
        {
            "name": "Semantic Scholar",
            "priority": 2,
            "status": "planned",
            "reason": "后续可用于补充引用、影响力和跨出版源结果。"
        },
        {
            "name": "OpenAlex",
            "priority": 3,
            "status": "planned",
            "reason": "后续可用于补充开放书目元数据和机构/主题聚合。"
        }
    ]


def normalize_source_priorities(raw_sources):
    """Ensure the source priority list contains the v1 planned sources."""
    defaults = build_source_priorities()
    if not isinstance(raw_sources, list):
        return defaults

    normalized = []
    seen = set()
    for idx, source in enumerate(raw_sources, start=1):
        if isinstance(source, str):
            source = {"name": source}
        if not isinstance(source, dict):
            continue
        name = text_value(source.get("name"))
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        status = text_value(source.get("status"), "planned")
        if key == "arxiv":
            status = "executed"
        normalized.append({
            "name": name,
            "priority": source.get("priority", idx),
            "status": status,
            "reason": text_value(source.get("reason"), "")
        })

    for source in defaults:
        key = source["name"].lower()
        if key not in seen:
            normalized.append(source)
            seen.add(key)

    def priority_value(source):
        try:
            return int(source.get("priority", 99))
        except Exception:
            return 99

    normalized.sort(key=priority_value)
    return normalized


def make_llm_payload(model_name, system_prompt, user_prompt, max_tokens, temperature=0.2):
    """Build a JSON-mode chat completions payload."""
    return {
        "model": model_name,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
        "response_format": {"type": "json_object"}
    }


def call_json_llm(api_url, api_key, payload, helpers, timeout):
    """Call the configured LLM and parse a JSON object from its text output."""
    data = helpers["post_llm_json_request"](
        api_url,
        api_key,
        payload,
        timeout=timeout,
        allow_response_format_fallback=True
    )
    content = helpers["extract_llm_response_text"](data)
    parsed = helpers["parse_json_like"](content)
    if not isinstance(parsed, dict):
        preview = helpers["truncate_text"](content, 240)
        raise Exception(f"LLM 未返回可解析的 JSON 对象: {preview}")
    return parsed, data.get("usage", {}), content


def build_planner_prompt(question, max_subqueries):
    """Create the planner prompt for intent and sub-query generation."""
    system_prompt = (
        "你是一名学术检索策略 agent。你必须只输出一个可被 json.loads 解析的 JSON 对象，"
        "不要输出 Markdown、代码块或解释文字。"
    )
    user_prompt = f"""请把用户的复杂研究问题拆解为可执行的 arXiv 检索策略。

用户问题：{question}

要求：
1. 识别用户意图，特别是研究对象、任务领域、时间倾向、关键术语、同义表达和排除范围。
2. 生成 {max_subqueries} 个英文 arXiv 子查询，覆盖：宽泛综述、核心任务、方法路线、评测/benchmark、应用或局限。
3. query 只能包含英文关键词或英文引号短语，例如 "large language model" "automated program repair"；不要包含 AND、OR、cat:、all: 或括号语法。
4. 每个子查询给出 arxivCategories，优先从 cs.SE、cs.AI、cs.LG、cs.CL、cs.PL、cs.IR 中选择。
5. sourcePriorities 必须包含 arXiv、Semantic Scholar、OpenAlex；arXiv status 为 executed，其余为 planned。
6. 如果问题是中文，请将核心检索词扩展成常用英文术语。

请严格返回如下 JSON 结构：
{{
  "intent": {{
    "researchTopic": "中文概括",
    "taskDomain": "中文领域",
    "keyEntities": ["术语1"],
    "synonyms": ["英文同义词"],
    "timeSensitivity": "latest-progress",
    "inScope": ["范围"],
    "outOfScope": ["排除范围"]
  }},
  "strategy": {{
    "overview": "中文策略说明",
    "searchAngles": ["角度1", "角度2"]
  }},
  "sourcePriorities": [
    {{"name": "arXiv", "priority": 1, "status": "executed", "reason": "中文原因"}}
  ],
  "subQueries": [
    {{
      "id": "q1",
      "angle": "宽泛综述",
      "query": "\\"large language model\\" \\"automated program repair\\"",
      "arxivCategories": ["cs.SE", "cs.AI"],
      "rationale": "中文说明"
    }}
  ]
}}"""
    return system_prompt, user_prompt


def text_value(value, fallback=""):
    """Return a compact string from arbitrary JSON-like values."""
    if isinstance(value, list):
        value = " ".join(str(item) for item in value)
    return re.sub(r"\s+", " ", str(value or fallback).strip())


def normalize_categories(categories):
    """Keep only supported arXiv CS category codes."""
    if not isinstance(categories, list):
        return list(DEFAULT_AGENT_CATEGORIES)
    normalized = []
    for category in categories:
        code = str(category or "").strip()
        if code in ALLOWED_AGENT_CATEGORIES and code not in normalized:
            normalized.append(code)
    return normalized or list(DEFAULT_AGENT_CATEGORIES)


def sanitize_query(query):
    """Keep the LLM query compatible with the existing keyword parser."""
    query = text_value(query)
    query = query.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
    query = re.sub(r"\b(?:AND|OR|NOT)\b", " ", query, flags=re.IGNORECASE)
    query = re.sub(r"\b(?:all|ti|abs|cat)\s*:", " ", query, flags=re.IGNORECASE)
    query = re.sub(r"[()\[\]{}]", " ", query)
    query = re.sub(r"\s+", " ", query).strip()
    return query[:220].strip()


def first_list_from_containers(containers, keys):
    """Find the first list value across several dict containers and key aliases."""
    for container in containers:
        if not isinstance(container, dict):
            continue
        lowered = {str(k).lower(): v for k, v in container.items()}
        for key in keys:
            if key in container and isinstance(container[key], list):
                return container[key]
            lower_key = str(key).lower()
            if lower_key in lowered and isinstance(lowered[lower_key], list):
                return lowered[lower_key]
    return []


def collect_query_like_items(value, found=None, allow_string=False):
    """Recursively collect query-like objects or strings from flexible planner JSON."""
    if found is None:
        found = []
    if len(found) >= DEFAULT_MAX_SUBQUERIES:
        return found

    query_keys = {
        "query", "keywords", "searchquery", "search_query", "arxivquery", "arxiv_query",
        "querystring", "query_string", "terms", "query_terms", "searchterms", "search_terms",
        "关键词", "检索词", "查询"
    }
    list_keys = {
        "subqueries", "queries", "searchqueries", "search_queries", "arxivqueries",
        "arxiv_queries", "queryplan", "query_plan", "expandedqueries", "expanded_queries",
        "refinedqueries", "refined_queries", "steps", "检索查询", "子查询", "查询"
    }

    if isinstance(value, list):
        for item in value:
            collect_query_like_items(item, found, allow_string=allow_string)
            if len(found) >= DEFAULT_MAX_SUBQUERIES:
                break
        return found

    if isinstance(value, str):
        if allow_string and sanitize_query(value):
            found.append(value)
        return found

    if not isinstance(value, dict):
        return found

    lowered = {str(key).lower(): val for key, val in value.items()}
    if any(key in lowered for key in query_keys):
        found.append(value)
        return found

    for key, item in value.items():
        if str(key).lower() in list_keys:
            collect_query_like_items(item, found, allow_string=True)
            if len(found) >= DEFAULT_MAX_SUBQUERIES:
                return found

    for item in value.values():
        collect_query_like_items(item, found, allow_string=False)
        if len(found) >= DEFAULT_MAX_SUBQUERIES:
            break
    return found


def format_query_value(value):
    """Format list-valued query terms into a stable keyword query."""
    if not isinstance(value, list):
        return value
    parts = []
    for item in value:
        term = text_value(item)
        if not term:
            continue
        if " " in term and not (term.startswith('"') and term.endswith('"')):
            term = f'"{term}"'
        parts.append(term)
    return " ".join(parts)


def english_query_terms_from_question(question, intent):
    """Build conservative fallback English query terms from the original question and intent."""
    text = f"{question} {text_value(intent.get('researchTopic')) if isinstance(intent, dict) else ''} "
    text += text_value(intent.get("keyEntities")) if isinstance(intent, dict) else ""
    text += " " + (text_value(intent.get("synonyms")) if isinstance(intent, dict) else "")
    lower = text.lower()

    terms = []
    if re.search(r"llm|large language|大模型|language model", lower):
        terms.append('"large language model"')
    if re.search(r"缺陷|bug|defect|repair|修复|program repair|自动程序修复", lower):
        terms.extend(['"automated program repair"', '"bug fixing"'])
    if re.search(r"软件|software|program|code|代码", lower):
        terms.append('"software engineering"')
    if re.search(r"rag|retrieval", lower):
        terms.append('"retrieval augmented generation"')
    if re.search(r"agent|multi.agent|智能体|多智能体", lower):
        terms.append('"multi-agent"')

    english_tokens = re.findall(r"[A-Za-z][A-Za-z0-9+\-]{2,}", text)
    for token in english_tokens:
        lowered = token.lower()
        if lowered not in {"the", "and", "for", "with", "recent", "progress", "latest"}:
            terms.append(token)

    unique_terms = []
    seen = set()
    for term in terms:
        key = term.lower()
        if key not in seen:
            unique_terms.append(term)
            seen.add(key)

    if not unique_terms:
        unique_terms = ['"large language model"', '"software engineering"']
    return " ".join(unique_terms[:6])


def fallback_subqueries(question, intent, max_subqueries):
    """Create safe fallback subqueries when a model returns a non-conforming plan."""
    base_query = sanitize_query(english_query_terms_from_question(question, intent))
    variants = [
        ("fallback-q1", "保守兜底检索", base_query, "模型未返回标准子查询，使用问题关键词生成保守检索式。"),
        ("fallback-q2", "方法与评测补充", f'{base_query} benchmark evaluation', "补充评测和 benchmark 相关论文。"),
        ("fallback-q3", "挑战与局限补充", f'{base_query} challenge limitation', "补充局限和挑战相关论文。")
    ]
    subqueries = []
    for idx, (query_id, angle, query, rationale) in enumerate(variants[:max_subqueries], start=1):
        subqueries.append({
            "id": query_id or f"fallback-q{idx}",
            "angle": angle,
            "query": sanitize_query(query),
            "arxivCategories": list(DEFAULT_AGENT_CATEGORIES),
            "rationale": rationale,
            "resultCount": 0,
            "error": ""
        })
    return [item for item in subqueries if item["query"]]


RELAXED_QUERY_STOPWORDS = {
    "survey", "surveys", "overview", "benchmark", "benchmarks", "evaluation", "evaluations",
    "limitation", "limitations", "challenge", "challenges", "recent", "progress", "latest",
    "method", "methods", "approach", "approaches", "study", "studies", "paper", "papers",
    "quality", "hallucination", "hallucinations", "transformer", "transformers"
}


def quote_query_unit(unit):
    """Quote a multi-word query unit for the existing keyword parser."""
    unit = sanitize_query(unit).strip('"')
    if not unit:
        return ""
    if " " in unit:
        return f'"{unit}"'
    return unit


def extract_query_units(query):
    """Extract phrase/token units from a planner query and drop generic modifiers."""
    query = sanitize_query(query)
    units = []
    for quoted, token in re.findall(r'"([^"]+)"|([A-Za-z][A-Za-z0-9+\-_.]{2,})', query):
        unit = sanitize_query(quoted or token).strip('"').lower()
        if not unit or unit in RELAXED_QUERY_STOPWORDS:
            continue
        units.append(unit)

    unique = []
    seen = set()
    for unit in units:
        if unit not in seen:
            unique.append(unit)
            seen.add(unit)
    return unique


def relaxed_query_candidates(query, question, intent):
    """Build less brittle retries for arXiv when a strict generated query gets 0 papers."""
    units = extract_query_units(query)
    candidates = []

    priority_phrases = [
        "automated program repair",
        "program repair",
        "bug fixing",
        "bug repair",
        "defect repair",
        "code repair",
        "vulnerability repair",
        "software repair",
        "patch generation",
        "large language model",
        "code generation"
    ]

    for phrase in priority_phrases:
        if any(phrase in unit for unit in units):
            candidates.append(quote_query_unit(phrase))

    phrase_units = [unit for unit in units if " " in unit]
    for unit in phrase_units:
        candidates.append(quote_query_unit(unit))

    non_generic = [unit for unit in units if unit not in RELAXED_QUERY_STOPWORDS]
    if len(non_generic) >= 2:
        candidates.append(" ".join(quote_query_unit(unit) for unit in non_generic[:2]))
    if len(non_generic) >= 1:
        candidates.append(quote_query_unit(non_generic[0]))

    fallback_query = english_query_terms_from_question(question, intent)
    candidates.append(fallback_query)

    normalized_original = sanitize_query(query).lower()
    unique = []
    seen = {normalized_original}
    for candidate in candidates:
        candidate = sanitize_query(candidate)
        key = candidate.lower()
        if candidate and key not in seen:
            unique.append(candidate)
            seen.add(key)
    return unique[:4]


def normalize_planner_result(parsed, question, max_subqueries):
    """Validate and stabilize the planner JSON."""
    intent = parsed.get("intent") if isinstance(parsed.get("intent"), dict) else {}
    strategy = parsed.get("strategy") if isinstance(parsed.get("strategy"), dict) else {}
    nested_containers = [
        parsed,
        parsed.get("strategy"),
        parsed.get("searchStrategy"),
        parsed.get("search_strategy"),
        parsed.get("searchPlan"),
        parsed.get("search_plan"),
        parsed.get("plan"),
        parsed.get("检索策略"),
        parsed.get("搜索策略")
    ]
    source_priorities = normalize_source_priorities(
        first_list_from_containers(nested_containers, [
            "sourcePriorities", "source_priorities", "sources", "dataSources", "data_sources",
            "数据源优先级", "数据源"
        ])
    )

    raw_queries = first_list_from_containers(nested_containers, [
        "subQueries", "subqueries", "queries", "searchQueries", "search_queries",
        "arxivQueries", "arxiv_queries", "queryPlan", "query_plan", "检索查询", "子查询", "查询"
    ])
    if not raw_queries:
        raw_queries = collect_query_like_items(parsed)[:max_subqueries]

    subqueries = []
    for idx, item in enumerate(raw_queries[:max_subqueries], start=1):
        if isinstance(item, str):
            item = {"query": item}
        if not isinstance(item, dict):
            continue

        lowered_item = {str(k).lower(): v for k, v in item.items()}
        query_value = (
            item.get("query") or item.get("keywords") or item.get("searchQuery") or
            item.get("search_query") or item.get("arxivQuery") or item.get("arxiv_query") or
            item.get("queryString") or item.get("query_string") or item.get("terms") or
            item.get("query_terms") or item.get("searchTerms") or item.get("search_terms") or
            item.get("关键词") or item.get("检索词") or item.get("查询") or
            lowered_item.get("query_terms") or lowered_item.get("searchterms") or
            lowered_item.get("search_terms")
        )
        query = sanitize_query(format_query_value(query_value))
        if not query:
            continue

        subqueries.append({
            "id": text_value(item.get("id"), f"q{idx}")[:16] or f"q{idx}",
            "angle": text_value(item.get("angle") or item.get("type"), f"检索角度 {idx}")[:80],
            "query": query,
            "arxivCategories": normalize_categories(item.get("arxivCategories") or item.get("categories")),
            "rationale": text_value(item.get("rationale") or item.get("reason"), "")[:240],
            "resultCount": 0,
            "error": ""
        })

    if not subqueries:
        subqueries = fallback_subqueries(question, intent, max_subqueries)

    if not intent:
        intent = {
            "researchTopic": question,
            "taskDomain": "",
            "keyEntities": [],
            "synonyms": [],
            "timeSensitivity": "latest-progress",
            "inScope": [],
            "outOfScope": []
        }

    if not strategy:
        strategy = {
            "overview": "围绕用户问题生成多角度 arXiv 子查询，并对命中论文进行去重、排序和综述。",
            "searchAngles": [item["angle"] for item in subqueries]
        }

    return {
        "intent": intent,
        "strategy": strategy,
        "sourcePriorities": source_priorities,
        "subQueries": subqueries
    }


def parse_date(value):
    """Parse a YYYY-MM-DD date for recency scoring."""
    try:
        return datetime.fromisoformat(str(value or "")[:10])
    except Exception:
        return datetime(1970, 1, 1)


def query_terms(query):
    """Extract rough content terms from an arXiv keyword query."""
    terms = []
    for quoted, token in re.findall(r'"([^"]+)"|([A-Za-z][A-Za-z0-9+\-_.]{2,})', query):
        value = (quoted or token).strip().lower()
        if value and value not in {"large", "language", "model", "models", "paper", "study"}:
            terms.append(value)
    return terms


def score_paper(paper, subqueries, matched_query_ids):
    """Score papers using lightweight topical match and recency signals."""
    text = f"{paper.get('title', '')} {paper.get('abstract', '')}".lower()
    terms = []
    for subquery in subqueries:
        if subquery["id"] in matched_query_ids:
            terms.extend(query_terms(subquery.get("query", "")))
    unique_terms = list(dict.fromkeys(terms))

    if unique_terms:
        matched_terms = sum(1 for term in unique_terms if term.lower() in text)
        topical_score = matched_terms / max(1, len(unique_terms))
    else:
        topical_score = 0

    published = parse_date(paper.get("published", ""))
    age_days = max(0, (datetime.utcnow() - published).days)
    recency_score = max(0, 1 - min(age_days, 365) / 365)
    query_score = min(1, len(matched_query_ids) / max(1, len(subqueries)))

    return round(0.55 * topical_score + 0.30 * query_score + 0.15 * recency_score, 4)


def dedupe_and_rank(query_results, subqueries, max_papers):
    """Deduplicate arXiv entries and rank final papers."""
    by_id = {}
    for subquery in subqueries:
        for paper in query_results.get(subquery["id"], []):
            paper_id = str(paper.get("id") or paper.get("link") or paper.get("title") or "").strip()
            if not paper_id:
                continue
            if paper_id not in by_id:
                copied = dict(paper)
                copied["matchSubQueries"] = []
                copied["matchAngles"] = []
                by_id[paper_id] = copied
            if subquery["id"] not in by_id[paper_id]["matchSubQueries"]:
                by_id[paper_id]["matchSubQueries"].append(subquery["id"])
            if subquery["angle"] not in by_id[paper_id]["matchAngles"]:
                by_id[paper_id]["matchAngles"].append(subquery["angle"])

    ranked = []
    for paper in by_id.values():
        paper["relevanceScore"] = score_paper(paper, subqueries, set(paper["matchSubQueries"]))
        ranked.append(paper)

    ranked.sort(
        key=lambda item: (
            -item.get("relevanceScore", 0),
            -parse_date(item.get("published", "")).timestamp()
        )
    )
    return ranked[:max_papers], ranked


def create_research_tool_registry(fetch_papers, time_range, papers_per_query):
    """Create v1 agent tools. More literature sources can be added here later."""
    registry = ToolRegistry()

    def arxiv_runner(parameters):
        categories = parameters.get("categories") or DEFAULT_AGENT_CATEGORIES
        query = parameters.get("query", "")
        return fetch_papers(categories, time_range, papers_per_query, query)

    registry.register(AgentTool(
        "arxiv_search",
        "Search arXiv papers by categories, time range and English keyword query.",
        arxiv_runner
    ))
    return registry


def execute_arxiv_tool(subquery, query, time_range, papers_per_query, fetch_papers,
                       tool_registry=None, trajectory=None, note=""):
    """Execute one arXiv search through the tool registry when available."""
    categories = subquery.get("arxivCategories", DEFAULT_AGENT_CATEGORIES)
    parameters = {
        "queryId": subquery.get("id", ""),
        "angle": subquery.get("angle", ""),
        "query": query,
        "categories": categories,
        "timeRange": time_range,
        "papersPerQuery": papers_per_query
    }

    if trajectory:
        trajectory.add(
            "action",
            f"调用 arXiv 检索工具 {subquery.get('id', '')}",
            content=note or subquery.get("rationale", ""),
            tool="arxiv_search",
            input_data=parameters
        )

    if tool_registry:
        papers = tool_registry.execute("arxiv_search", parameters)
    else:
        papers = fetch_papers(categories, time_range, papers_per_query, query)

    if trajectory:
        trajectory.add(
            "observation",
            f"{subquery.get('id', '')} 返回 {len(papers)} 篇论文",
            tool="arxiv_search",
            output_summary=f"命中 {len(papers)} 篇",
            metadata={
                "queryId": subquery.get("id", ""),
                "query": query,
                "resultCount": len(papers)
            },
            status="success" if papers else "warning"
        )
    return papers


def run_arxiv_searches(plan, question, time_range, papers_per_query, fetch_papers,
                       safe_print, tool_registry=None, trajectory=None):
    """Execute each planner sub-query against arXiv."""
    query_results = {}
    rate_limited = False
    for subquery in plan["subQueries"]:
        if rate_limited:
            subquery["resultCount"] = 0
            subquery["error"] = "arXiv API 请求频率过高，本次已跳过剩余子查询"
            query_results[subquery["id"]] = []
            if trajectory:
                trajectory.add(
                    "observation",
                    f"跳过 {subquery.get('id', '')}",
                    content=subquery["error"],
                    status="warning",
                    metadata={"queryId": subquery.get("id", "")}
                )
            continue

        try:
            papers = execute_arxiv_tool(
                subquery,
                subquery.get("query", ""),
                time_range,
                papers_per_query,
                fetch_papers,
                tool_registry=tool_registry,
                trajectory=trajectory
            )
            if not papers:
                for relaxed_query in relaxed_query_candidates(subquery.get("query", ""), question, plan.get("intent", {})):
                    safe_print(f"[agent] {subquery['id']} 原查询 0 篇，宽松重试: {relaxed_query}")
                    papers = execute_arxiv_tool(
                        subquery,
                        relaxed_query,
                        time_range,
                        papers_per_query,
                        fetch_papers,
                        tool_registry=tool_registry,
                        trajectory=trajectory,
                        note="原查询命中 0 篇，执行宽松关键词重试"
                    )
                    if papers:
                        subquery["relaxedQuery"] = relaxed_query
                        subquery["relaxationNote"] = "原查询命中 0 篇，已自动使用更宽松关键词重试"
                        break
            subquery["resultCount"] = len(papers)
            query_results[subquery["id"]] = papers
            safe_print(f"[agent] {subquery['id']} 命中 {len(papers)} 篇: {subquery['query']}")
        except Exception as exc:
            subquery["resultCount"] = 0
            subquery["error"] = str(exc)
            query_results[subquery["id"]] = []
            safe_print(f"[agent] {subquery['id']} 检索失败: {str(exc)}")
            if trajectory:
                trajectory.add(
                    "observation",
                    f"{subquery.get('id', '')} 检索失败",
                    content=str(exc),
                    tool="arxiv_search",
                    status="error",
                    metadata={"queryId": subquery.get("id", "")}
                )
            if "429" in str(exc) or "频率过高" in str(exc) or "Too Many Requests" in str(exc):
                rate_limited = True
    return query_results


def compact_paper_for_synthesis(paper, index):
    """Reduce a paper to the fields needed by the synthesis prompt."""
    return {
        "index": index,
        "title": paper.get("title", ""),
        "abstract": paper.get("abstract", "")[:900],
        "authors": paper.get("authors", [])[:5],
        "published": paper.get("published", ""),
        "primaryCategory": paper.get("primaryCategory", ""),
        "categories": paper.get("categories", [])[:6],
        "link": paper.get("link", ""),
        "matchAngles": paper.get("matchAngles", []),
        "relevanceScore": paper.get("relevanceScore", 0)
    }


def build_synthesis_prompt(question, plan, papers):
    """Create the synthesis prompt for final knowledge extraction."""
    system_prompt = (
        "你是一名严谨的中文科研情报分析师。你必须只输出一个可被 json.loads 解析的 JSON 对象，"
        "不要输出 Markdown、代码块或额外解释。"
    )
    compact_papers = [
        compact_paper_for_synthesis(paper, idx)
        for idx, paper in enumerate(papers[:TOP_PAPERS_FOR_SYNTHESIS], start=1)
    ]
    user_prompt = f"""请基于检索到的 arXiv 论文，对用户研究问题做智能综述和知识提炼。

用户问题：{question}

查询理解：
{json.dumps(plan.get("intent", {}), ensure_ascii=False)}

搜索策略：
{json.dumps(plan.get("subQueries", []), ensure_ascii=False)}

论文列表：
{json.dumps(compact_papers, ensure_ascii=False)}

要求：
1. 所有结论必须基于论文标题、摘要、分类和检索命中信息，不要编造论文没有支持的细节。
2. 用中文输出，英文术语可保留英文。
3. keyTrends、methodMap、representativePapers 中的 evidence 使用论文列表里的 index。
4. 如果证据不足，请明确写出证据不足，不要强行总结。

请严格返回如下 JSON 结构：
{{
  "overview": "2-4 段中文综述",
  "keyTrends": [
    {{"label": "趋势名称", "summary": "趋势说明", "evidence": [1, 2]}}
  ],
  "methodMap": [
    {{"method": "方法/技术路线", "description": "中文说明", "evidence": [1]}}
  ],
  "representativePapers": [
    {{"index": 1, "title": "论文标题", "reason": "为何代表"}}
  ],
  "limitations": ["局限或证据边界"],
  "futureDirections": ["后续检索或研究方向"]
}}"""
    return system_prompt, user_prompt


def normalize_evidence(value, max_index):
    """Normalize evidence paper indexes."""
    if not isinstance(value, list):
        value = [value]
    normalized = []
    for item in value:
        try:
            number = int(item)
        except Exception:
            continue
        if 1 <= number <= max_index and number not in normalized:
            normalized.append(number)
    return normalized[:5]


def normalize_synthesis(parsed, papers):
    """Stabilize the synthesis JSON for the frontend."""
    max_index = len(papers)
    overview = text_value(parsed.get("overview") or parsed.get("summary") or parsed.get("综述"))
    if not overview:
        overview = "未能生成综述内容。"

    key_trends = []
    for item in parsed.get("keyTrends") or parsed.get("trends") or parsed.get("趋势") or []:
        if not isinstance(item, dict):
            continue
        key_trends.append({
            "label": text_value(item.get("label") or item.get("name") or item.get("趋势"))[:80],
            "summary": text_value(item.get("summary") or item.get("description") or item.get("说明"))[:500],
            "evidence": normalize_evidence(item.get("evidence") or item.get("papers"), max_index)
        })

    method_map = []
    for item in parsed.get("methodMap") or parsed.get("methods") or parsed.get("方法") or []:
        if not isinstance(item, dict):
            continue
        method_map.append({
            "method": text_value(item.get("method") or item.get("label") or item.get("name"))[:80],
            "description": text_value(item.get("description") or item.get("summary") or item.get("说明"))[:500],
            "evidence": normalize_evidence(item.get("evidence") or item.get("papers"), max_index)
        })

    representative = []
    for item in parsed.get("representativePapers") or parsed.get("papers") or parsed.get("代表论文") or []:
        if not isinstance(item, dict):
            continue
        try:
            index = int(item.get("index"))
        except Exception:
            index = 0
        if 1 <= index <= max_index:
            paper_title = papers[index - 1].get("title", "")
        else:
            paper_title = text_value(item.get("title"))
        representative.append({
            "index": index,
            "title": paper_title,
            "reason": text_value(item.get("reason") or item.get("summary") or item.get("说明"))[:360]
        })

    limitations = [
        text_value(item)[:300]
        for item in (parsed.get("limitations") or parsed.get("局限") or [])
        if text_value(item)
    ]
    future_directions = [
        text_value(item)[:300]
        for item in (parsed.get("futureDirections") or parsed.get("directions") or parsed.get("后续方向") or [])
        if text_value(item)
    ]

    return {
        "overview": overview,
        "keyTrends": key_trends[:8],
        "methodMap": method_map[:8],
        "representativePapers": representative[:8],
        "limitations": limitations[:8],
        "futureDirections": future_directions[:8]
    }


def empty_synthesis():
    """Return a stable synthesis object for empty-search cases."""
    return {
        "overview": "在当前时间范围和子查询下未检索到足够的 arXiv 论文，无法形成可靠综述。建议放宽时间范围、减少限定词，或使用更宽泛的英文术语重新检索。",
        "keyTrends": [],
        "methodMap": [],
        "representativePapers": [],
        "limitations": ["当前结果集为空，无法判断趋势。"],
        "futureDirections": ["放宽时间范围", "增加同义英文关键词", "后续接入 Semantic Scholar 或 OpenAlex 进行补充检索"]
    }


def evidence_count(items):
    """Count distinct evidence ids in normalized synthesis items."""
    evidence = set()
    for item in items or []:
        if isinstance(item, dict):
            for idx in item.get("evidence", []) or []:
                try:
                    evidence.add(int(idx))
                except Exception:
                    continue
    return len(evidence)


def evaluate_agent_result(question, plan, papers, synthesis, stats):
    """Deterministic reflection step for result quality and coverage."""
    subqueries = plan.get("subQueries", [])
    zero_queries = [item for item in subqueries if int(item.get("resultCount") or 0) == 0]
    relaxed_queries = [item for item in subqueries if item.get("relaxedQuery")]
    search_errors = [item for item in subqueries if item.get("error")]
    final_count = len(papers or [])
    trend_evidence = evidence_count(synthesis.get("keyTrends", []))
    method_evidence = evidence_count(synthesis.get("methodMap", []))

    score = 100
    issues = []
    recommendations = []

    if final_count == 0:
        score -= 65
        issues.append("当前 arXiv 检索结果为空，无法支撑可靠综述。")
        recommendations.append("放宽时间范围，减少限定词，或改用更宽泛的英文核心术语重新检索。")
    elif final_count < 3:
        score -= 25
        issues.append("代表论文数量偏少，趋势判断需要谨慎。")
        recommendations.append("增加每个子查询的论文数，或扩大到近 365 天。")
    elif final_count < 8:
        score -= 10
        recommendations.append("结果数量中等，适合作为快速扫描；若用于正式综述建议扩大检索范围。")

    if subqueries and len(zero_queries) == len(subqueries):
        score -= 20
        issues.append("所有子查询均未命中，说明检索表达或时间范围可能过窄。")
    elif zero_queries:
        score -= min(15, len(zero_queries) * 4)
        recommendations.append(f"{len(zero_queries)} 个子查询无命中，可对这些角度补充同义词或放宽短语匹配。")

    if relaxed_queries:
        recommendations.append(f"{len(relaxed_queries)} 个子查询触发了宽松重试，报告中应优先参考实际重试查询的命中结果。")

    if search_errors:
        score -= min(25, len(search_errors) * 10)
        issues.append(f"{len(search_errors)} 个子查询检索失败，覆盖面不完整。")

    if final_count and not synthesis.get("keyTrends"):
        score -= 10
        issues.append("综述未形成关键趋势列表，知识提炼粒度不足。")
    if final_count and not synthesis.get("methodMap"):
        score -= 8
        recommendations.append("方法路线提炼为空，建议增加方法/benchmark 角度的子查询。")
    if final_count and trend_evidence == 0 and method_evidence == 0:
        score -= 8
        issues.append("趋势或方法结论缺少可追溯证据编号。")

    score = max(0, min(100, score))
    if final_count == 0:
        coverage_level = "empty"
        coverage_label = "空结果"
    elif score >= 80:
        coverage_level = "good"
        coverage_label = "覆盖较好"
    elif score >= 55:
        coverage_level = "limited"
        coverage_label = "覆盖有限"
    else:
        coverage_level = "weak"
        coverage_label = "证据较弱"

    return {
        "score": score,
        "coverageLevel": coverage_level,
        "coverageLabel": coverage_label,
        "issues": issues[:6],
        "recommendations": recommendations[:8],
        "evidencePaperCount": final_count,
        "trendEvidenceCount": trend_evidence,
        "methodEvidenceCount": method_evidence,
        "zeroResultQueryCount": len(zero_queries),
        "relaxedQueryCount": len(relaxed_queries),
        "searchErrorCount": len(search_errors)
    }


def run_research_agent(question, time_range, max_papers, papers_per_query, llm_config,
                       fetch_papers, helpers, safe_print=print, max_subqueries=DEFAULT_MAX_SUBQUERIES):
    """Run the full research-agent workflow."""
    question = normalize_question(question)
    if not question:
        raise Exception("研究问题不能为空")

    api_url = normalize_api_url(llm_config.get("endpoint", ""))
    api_key = llm_config.get("key", "")
    model_name = llm_config.get("model", "")
    if not api_url or not model_name:
        raise Exception("LLM API 地址和模型名称不能为空")

    time_range = clamp_int(time_range, DEFAULT_TIME_RANGE, 1, 3650)
    max_papers = clamp_int(max_papers, DEFAULT_MAX_PAPERS, 1, 80)
    papers_per_query = clamp_int(papers_per_query, DEFAULT_PAPERS_PER_QUERY, 1, 50)
    max_subqueries = clamp_int(max_subqueries, DEFAULT_MAX_SUBQUERIES, 1, DEFAULT_MAX_SUBQUERIES)

    started = time.time()
    trajectory = AgentTrajectory()
    trajectory.add(
        "task",
        "接收研究问题",
        content=question,
        metadata={
            "timeRange": time_range,
            "maxPapers": max_papers,
            "papersPerQuery": papers_per_query,
            "maxSubQueries": max_subqueries
        }
    )
    safe_print(f"[agent] 开始研究任务: {question[:80]}")

    planner_system, planner_user = build_planner_prompt(question, max_subqueries)
    planner_payload = make_llm_payload(model_name, planner_system, planner_user, 2400, temperature=0.1)
    trajectory.add(
        "thought",
        "生成检索计划",
        content="调用 LLM 进行查询理解、意图识别和多角度子查询规划。",
        metadata={"model": model_name}
    )
    planner_json, planner_usage, _ = call_json_llm(api_url, api_key, planner_payload, helpers, timeout=180)
    plan = normalize_planner_result(planner_json, question, max_subqueries)
    trajectory.add(
        "plan",
        "形成多步搜索策略",
        content=plan.get("strategy", {}).get("overview", ""),
        metadata={
            "subQueryCount": len(plan.get("subQueries", [])),
            "sourceCount": len(plan.get("sourcePriorities", [])),
            "plannerTokens": planner_usage.get("total_tokens", 0)
        }
    )

    tool_registry = create_research_tool_registry(fetch_papers, time_range, papers_per_query)
    trajectory.add(
        "tooling",
        "注册可执行检索工具",
        content="当前版本执行 arXiv 检索工具，并保留多源工具扩展接口。",
        metadata={"tools": tool_registry.describe()}
    )
    query_results = run_arxiv_searches(
        plan,
        question,
        time_range,
        papers_per_query,
        fetch_papers,
        safe_print,
        tool_registry=tool_registry,
        trajectory=trajectory
    )
    final_papers, all_ranked_papers = dedupe_and_rank(query_results, plan["subQueries"], max_papers)
    trajectory.add(
        "observation",
        "完成去重与相关性排序",
        output_summary=f"检索 {sum(len(items) for items in query_results.values())} 条，去重 {len(all_ranked_papers)} 条，输出 {len(final_papers)} 条。",
        metadata={
            "retrievedPapers": sum(len(items) for items in query_results.values()),
            "uniquePapers": len(all_ranked_papers),
            "finalPapers": len(final_papers)
        },
        status="success" if final_papers else "warning"
    )

    synthesis_usage = {}
    if final_papers:
        synthesis_system, synthesis_user = build_synthesis_prompt(question, plan, final_papers)
        synthesis_payload = make_llm_payload(model_name, synthesis_system, synthesis_user, 3600, temperature=0.2)
        trajectory.add(
            "thought",
            "生成智能综述",
            content="调用 LLM 基于 Top papers 提炼趋势、方法、代表论文、局限和后续方向。",
            metadata={"paperCountForSynthesis": min(len(final_papers), TOP_PAPERS_FOR_SYNTHESIS)}
        )
        synthesis_json, synthesis_usage, _ = call_json_llm(api_url, api_key, synthesis_payload, helpers, timeout=240)
        synthesis = normalize_synthesis(synthesis_json, final_papers)
        trajectory.add(
            "observation",
            "完成知识提炼",
            output_summary=f"趋势 {len(synthesis.get('keyTrends', []))} 条，方法 {len(synthesis.get('methodMap', []))} 条。",
            metadata={
                "synthesisTokens": synthesis_usage.get("total_tokens", 0),
                "trendCount": len(synthesis.get("keyTrends", [])),
                "methodCount": len(synthesis.get("methodMap", []))
            }
        )
    else:
        synthesis = empty_synthesis()
        trajectory.add(
            "observation",
            "跳过智能综述",
            content="检索结果为空，返回空结果说明和后续检索建议。",
            status="warning"
        )

    total_time = round(time.time() - started, 2)
    search_errors = [item for item in plan["subQueries"] if item.get("error")]
    stats = {
        "totalTime": total_time,
        "timeRange": time_range,
        "totalQueries": len(plan["subQueries"]),
        "retrievedPapers": sum(len(items) for items in query_results.values()),
        "uniquePapers": len(all_ranked_papers),
        "finalPapers": len(final_papers),
        "papersPerQuery": papers_per_query,
        "plannerTokens": planner_usage.get("total_tokens", 0),
        "synthesisTokens": synthesis_usage.get("total_tokens", 0),
        "totalTokens": planner_usage.get("total_tokens", 0) + synthesis_usage.get("total_tokens", 0),
        "searchErrors": len(search_errors)
    }
    evaluation = evaluate_agent_result(question, plan, final_papers, synthesis, stats)
    stats["evaluationScore"] = evaluation["score"]
    trajectory.add(
        "reflection",
        "评估结果质量",
        content=f"{evaluation['coverageLabel']}，评分 {evaluation['score']}/100。",
        status="success" if evaluation["score"] >= 70 else "warning",
        metadata=evaluation
    )

    return {
        "question": question,
        "intent": plan["intent"],
        "strategy": plan["strategy"],
        "subQueries": plan["subQueries"],
        "sourcePriorities": plan["sourcePriorities"],
        "papers": final_papers,
        "synthesis": synthesis,
        "stats": stats,
        "evaluation": evaluation,
        "trajectory": trajectory.to_list()
    }
