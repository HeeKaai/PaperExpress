/**
 * PaperExpress - 智能化论文速递
 * 前端 JavaScript
 */

// 全局状态
let currentPapers = [];
let isProcessing = false;
let categoriesLoaded = false;
let selectedCategories = new Set();
let translateStartTime = 0;
let translationStats = null;
let currentRunConfig = null;
let trendRequestSeq = 0;
let currentAgentResult = null;
let isAgentProcessing = false;

// API 基础 URL - 后端和前端在同一个服务器上，使用相对路径
const API_BASE = '';

// DOM 元素
const configSection = document.getElementById('configSection');
const progressSection = document.getElementById('progressSection');
const resultsSection = document.getElementById('resultsSection');
const agentWorkspaceSection = document.getElementById('agentWorkspaceSection');
const agentResultsSection = document.getElementById('agentResultsSection');
const startBtn = document.getElementById('startBtn');
const backBtn = document.getElementById('backBtn');
const exportBtn = document.getElementById('exportBtn');
const openAgentPageBtn = document.getElementById('openAgentPageBtn');
const agentStartBtn = document.getElementById('agentStartBtn');
const agentWorkspaceBackBtn = document.getElementById('agentWorkspaceBackBtn');
const agentBackBtn = document.getElementById('agentBackBtn');
const agentExportBtn = document.getElementById('agentExportBtn');
const agentProgressPanel = document.getElementById('agentProgressPanel');
const agentProgressNote = document.getElementById('agentProgressNote');
const serverStatus = document.getElementById('serverStatus');
let agentProgressTimer = null;

const agentProgressFlow = [
    { key: 'intent', percent: 12, status: '正在理解研究问题...', note: '正在识别研究对象、任务边界和关键英文术语。' },
    { key: 'queries', percent: 28, status: '正在生成多角度子查询...', note: '正在把开放问题拆成扩展、细化、方法和评测角度。' },
    { key: 'search', percent: 52, status: '正在检索 arXiv...', note: '正在执行多路 arXiv 查询，并准备去重排序。' },
    { key: 'synthesis', percent: 74, status: '正在提炼综述...', note: '正在基于命中论文提炼趋势、方法路线、证据和局限。' },
    { key: 'render', percent: 90, status: '正在整理报告...', note: '正在组织查询理解、搜索策略、代表论文和综述结果。' }
];

// 初始化
document.addEventListener('DOMContentLoaded', () => {
    console.log('📄 PaperExpress 已加载');
    checkServerStatus();
    setupEventListeners();
});

// 检查服务器状态
async function checkServerStatus() {
    try {
        const response = await fetch(`${API_BASE}/api/categories`, {
            method: 'GET',
            mode: 'cors'
        });

        if (response.ok) {
            updateServerStatus('connected');
        } else {
            updateServerStatus('error', '服务器响应异常');
        }
    } catch (error) {
        updateServerStatus('error', '无法连接到后端服务器，请确保已运行 python server.py');
    }
}

// 更新服务器状态显示
function updateServerStatus(status, message = '') {
    const indicator = serverStatus.querySelector('.status-indicator');
    const text = serverStatus.querySelector('.status-text');

    indicator.className = 'status-indicator';

    switch (status) {
        case 'connected':
            indicator.classList.add('connected');
            text.textContent = '服务器已连接';
            break;
        case 'error':
            indicator.classList.add('error');
            text.textContent = message || '服务器连接失败';
            break;
        default:
            indicator.classList.add('checking');
            text.textContent = '正在检查服务器连接...';
    }
}

// 设置事件监听器
function setupEventListeners() {
    // 加载分类按钮
    document.getElementById('loadCategoriesBtn').addEventListener('click', loadCategories);

    // 加载配置按钮
    document.getElementById('loadConfigBtn').addEventListener('click', loadConfigFromServer);

    // 测试连接按钮
    document.getElementById('testConnectionBtn').addEventListener('click', async () => {
        await testConnection();
    });

    // 开始按钮
    startBtn.addEventListener('click', handleStart);

    // 研究 Agent
    openAgentPageBtn.addEventListener('click', () => {
        showSection('agentWorkspace');
        history.pushState(null, '', '#agent');
        window.scrollTo({ top: 0, behavior: 'smooth' });
    });
    agentStartBtn.addEventListener('click', handleAgentStart);
    agentWorkspaceBackBtn.addEventListener('click', () => {
        showSection('config');
        history.pushState(null, '', window.location.pathname + window.location.search);
    });
    agentBackBtn.addEventListener('click', () => {
        showSection('agentWorkspace');
        history.pushState(null, '', '#agent');
    });
    agentExportBtn.addEventListener('click', exportAgentMarkdown);
    document.getElementById('agentQuestion').addEventListener('input', updateAgentButton);
    document.querySelectorAll('[data-agent-example]').forEach(btn => {
        btn.addEventListener('click', () => {
            document.getElementById('agentQuestion').value = btn.dataset.agentExample || '';
            updateAgentButton();
        });
    });

    // 返回按钮
    backBtn.addEventListener('click', () => showSection('config'));

    // 导出按钮
    exportBtn.addEventListener('click', exportToMarkdown);

    // 首页历史记录按钮
    document.getElementById('homeHistoryBtn').addEventListener('click', openHistoryModal);

    // 关键词输入也可以独立触发搜索
    document.getElementById('keywordSearch').addEventListener('input', updateStartButton);

    updateAgentButton();
    if (window.location.hash === '#agent') {
        showSection('agentWorkspace');
    }
}

// 加载学科分类
async function loadCategories() {
    const container = document.getElementById('categoryContainer');
    const loadBtn = document.getElementById('loadCategoriesBtn');
    const searchInput = document.getElementById('categorySearch');

    container.classList.remove('hidden');
    loadBtn.disabled = true;
    loadBtn.textContent = '加载中...';
    searchInput.style.display = 'block';

    try {
        const response = await fetch(`${API_BASE}/api/categories`);
        if (!response.ok) throw new Error('获取分类失败');

        const data = await response.json();
        const categories = data.categories;

        // 保存所有分类用于搜索
        window.allCategories = categories;

        // 按后端分组显示，保持对齐
        const groups = {
            '人工智能相关': [],
            '软件工程与系统': [],
            '计算机理论学': [],
            '其他': []
        };

        // 根据后端分类定义直接分组
        const aiCodes = ['cs.AI', 'cs.CL', 'cs.CV', 'cs.LG', 'cs.RO', 'cs.CY', 'cs.HC', 'cs.MA', 'cs.SC', 'cs.ET'];
        const systemCodes = ['cs.SE', 'cs.OS', 'cs.DC', 'cs.DB', 'cs.SY', 'cs.PL', 'cs.SD', 'cs.AR', 'cs.FL', 'cs.PF'];
        const theoryCodes = ['cs.DS', 'cs.CC', 'cs.CG', 'cs.DM', 'cs.LO', 'cs.GT', 'cs.CR', 'cs.NA', 'cs.IT', 'cs.NE'];

        Object.entries(categories).forEach(([code, name]) => {
            if (aiCodes.includes(code)) {
                groups['人工智能相关'].push([code, name]);
            } else if (systemCodes.includes(code)) {
                groups['软件工程与系统'].push([code, name]);
            } else if (theoryCodes.includes(code)) {
                groups['计算机理论学'].push([code, name]);
            } else {
                groups['其他'].push([code, name]);
            }
        });

        // 保存分组信息
        window.categoryGroups = groups;

        // 渲染分类
        renderCategories(groups);

        // 设置搜索
        searchInput.addEventListener('input', (e) => {
            const query = e.target.value.toLowerCase().trim();
            if (!query) {
                renderCategories(groups);
                return;
            }

            // 过滤匹配的分类
            const filteredGroups = {};
            Object.entries(groups).forEach(([groupName, items]) => {
                const filtered = items.filter(([code, name]) =>
                    code.toLowerCase().includes(query) || name.toLowerCase().includes(query)
                );
                if (filtered.length > 0) {
                    filteredGroups[groupName] = filtered;
                }
            });

            renderCategories(filteredGroups);
        });

        categoriesLoaded = true;
        updateSelectedCategoriesDisplay();
        updateStartButton();

    } catch (error) {
        container.innerHTML = `<div class="loading-text" style="color: var(--error-color)">加载失败: ${escapeHtml(error.message)}</div>`;
    } finally {
        loadBtn.disabled = false;
        loadBtn.textContent = '📋 重新加载分类';
    }
}

// 渲染分类列表
function renderCategories(groups) {
    const container = document.getElementById('categoryContainer');
    const searchInput = document.getElementById('categorySearch');

    // 保留搜索框，清空其他内容
    Array.from(container.children).forEach(child => {
        if (child.id !== 'categorySearch') child.remove();
    });

    // 整体三列网格布局，前三个分组各占一列，其他占满三列
    container.style.display = 'grid';
    container.style.gridTemplateColumns = '1fr 1fr 1fr';
    container.style.gap = '16px';

    Object.entries(groups).forEach(([groupName, items], index) => {
        if (items.length === 0) return;

        const groupDiv = document.createElement('div');
        groupDiv.style.marginBottom = '0';
        if (groupName === '其他') {
            groupDiv.style.gridColumn = '1 / -1';
        }

        const groupTitle = document.createElement('div');
        groupTitle.textContent = groupName;
        groupTitle.style.fontWeight = '600';
        groupTitle.style.fontSize = '0.85rem';
        groupTitle.style.color = 'var(--text-secondary)';
        groupTitle.style.marginBottom = '8px';
        groupDiv.appendChild(groupTitle);

        const itemsDiv = document.createElement('div');
        itemsDiv.style.display = 'flex';
        itemsDiv.style.flexDirection = 'column';
        itemsDiv.style.gap = '8px';

        items.forEach(([code, name]) => {
            const label = document.createElement('label');
            label.className = 'checkbox-item';
            label.style.marginBottom = '0';

            const checkbox = document.createElement('input');
            checkbox.type = 'checkbox';
            checkbox.value = code;
            checkbox.addEventListener('change', () => {
                if (checkbox.checked) {
                    selectedCategories.add(code);
                } else {
                    selectedCategories.delete(code);
                }
                updateSelectedCategoriesDisplay();
                updateStartButton();
            });

            // 恢复之前的选中状态
            if (selectedCategories.has(code)) {
                checkbox.checked = true;
            }

            const span = document.createElement('span');
            span.innerHTML = `<span class="cat-code">${code}</span> ${name}`;

            label.appendChild(checkbox);
            label.appendChild(span);
            itemsDiv.appendChild(label);
        });

        groupDiv.appendChild(itemsDiv);
        container.appendChild(groupDiv);
    });
}

// 更新已选分类显示
function updateSelectedCategoriesDisplay() {
    const container = document.getElementById('selectedCategories');

    if (selectedCategories.size === 0) {
        container.innerHTML = '<span class="empty">未选择分类</span>';
        return;
    }

    container.innerHTML = Array.from(selectedCategories).map(code =>
        `<span class="tag">${code}</span>`
    ).join('');
}

// 更新开始按钮状态
function updateStartButton() {
    const hasCategories = selectedCategories.size > 0;
    const hasKeywords = document.getElementById('keywordSearch').value.trim().length > 0;
    const hasSearchCondition = hasCategories || hasKeywords;
    startBtn.disabled = !hasSearchCondition;
    if (!hasSearchCondition) {
        startBtn.title = '请至少选择一个学科分类或输入关键词';
    } else {
        startBtn.title = '';
    }
}

function updateAgentButton() {
    const question = document.getElementById('agentQuestion').value.trim();
    agentStartBtn.disabled = question.length === 0 || isAgentProcessing;
}

// 从服务器加载配置文件
async function loadConfigFromServer() {
    const btn = document.getElementById('loadConfigBtn');
    btn.disabled = true;
    btn.textContent = '加载中...';

    try {
        const response = await fetch(`${API_BASE}/api/config`);
        const result = await response.json();

        if (!result.success) {
            alert(`加载失败: ${result.message}`);
            return;
        }

        // 填充表单
        document.getElementById('llmEndpoint').value = result.config.endpoint || '';
        document.getElementById('llmKey').value = result.config.key || '';
        document.getElementById('llmModel').value = result.config.model || '';

        alert('✓ 配置加载成功');
    } catch (error) {
        alert(`加载失败: ${error.message}`);
    } finally {
        btn.disabled = false;
        btn.textContent = '📂 加载配置文件';
    }
}

// 测试连接
async function testConnection() {
    const resultDiv = document.getElementById('testResult');
    const btn = document.getElementById('testConnectionBtn');

    btn.disabled = true;
    btn.textContent = '测试中...';
    resultDiv.className = 'test-result';
    resultDiv.textContent = '正在测试连接...';

    const config = getLLMConfig();

    try {
        const response = await fetch(`${API_BASE}/api/test`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ llm: config })
        });

        const result = await response.json();

        if (result.success) {
            resultDiv.className = 'test-result success';
            resultDiv.textContent = `✓ ${result.message}`;
        } else {
            resultDiv.className = 'test-result error';
            resultDiv.textContent = `✗ ${result.message}`;
        }
    } catch (error) {
        resultDiv.className = 'test-result error';
        resultDiv.textContent = `✗ 连接失败: ${error.message}`;
    } finally {
        btn.disabled = false;
        btn.textContent = '🔗 测试连接';
    }
}

// 获取 LLM 配置
function getLLMConfig() {
    return {
        endpoint: document.getElementById('llmEndpoint').value.trim(),
        key: document.getElementById('llmKey').value.trim(),
        model: document.getElementById('llmModel').value.trim()
    };
}

// 处理开始按钮点击
async function handleStart() {
    if (isProcessing) return;

    const config = getConfig();

    if (config.categories.length === 0 && !config.keywords) {
        alert('请至少选择一个学科分类或输入关键词');
        return;
    }

    isProcessing = true;
    currentPapers = [];
    translateStartTime = Date.now();

    showSection('progress');
    stopAgentProgressTicker();
    hideAgentProgressPanel();
    document.getElementById('progressDetails').innerHTML = '';

    try {
        // 步骤1: 获取论文
        updateProgress(5, '正在获取论文数据...', '开始从 arXiv 获取论文...');

        const fetchResponse = await fetch(`${API_BASE}/api/fetch`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                categories: config.categories,
                keywords: config.keywords,
                timeRange: config.timeRange,
                maxPapers: config.maxPapers
            })
        });

        if (!fetchResponse.ok) {
            const error = await fetchResponse.json();
            throw new Error(error.error || '获取论文失败');
        }

        const fetchData = await fetchResponse.json();
        const papers = fetchData.papers || [];

        updateProgress(30, `已获取 ${papers.length} 篇论文`, `✓ 成功获取 ${papers.length} 篇论文`);

        if (papers.length === 0) {
            throw new Error('未找到符合条件的论文，请尝试调整分类或时间范围');
        }

        // 步骤2: 批量翻译论文（并发）
        updateProgress(35, `翻译中...`, `开始调用 LLM 并发翻译 ${papers.length} 篇论文 (${config.concurrency} 并发)...`);

        const translateResponse = await fetch(`${API_BASE}/api/translate_batch`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                papers: papers,
                llm: config.llm,
                concurrency: config.concurrency,
                categories: config.categories,
                keywords: config.keywords,
                timeRange: config.timeRange,
                maxPapers: config.maxPapers
            })
        });

        if (!translateResponse.ok) {
            throw new Error('批量翻译请求失败');
        }

        const translateData = await translateResponse.json();
        const results = translateData.results || [];
        translationStats = translateData.stats || {};

        // 如果命中缓存，使用缓存中的数据和 papers
        if (translateData.cached) {
            const cachedDate = new Date(translateData.cacheCreated).toLocaleString('zh-CN');
            updateProgress(80, '加载中...', `✓ 命中历史缓存 (${cachedDate})，正在加载...`);
        }

        // 合并翻译结果；命中缓存时优先使用缓存中的论文列表，避免与旧请求结果错位
        const sourcePapers = translateData.cached && Array.isArray(translateData.papers) && translateData.papers.length > 0
            ? translateData.papers
            : papers;
        const translatedPapers = sourcePapers.map((paper, i) => {
            const result = results[i] || { result: {} };
            const hasError = !result.success;
            return {
                ...paper,
                chineseAbstract: result.result.chineseAbstract || '解析失败',
                highlight: result.result.highlight || '解析失败',
                translationError: hasError,
                tokenUsage: {
                    promptTokens: result.result.promptTokens || 0,
                    completionTokens: result.result.completionTokens || 0,
                    totalTokens: result.result.totalTokens || 0
                }
            };
        });

        // 步骤3: 展示结果
        currentPapers = translatedPapers;
        renderResults(currentPapers, config, translationStats, translateData.cached);
        showSection('results');

        updateProgress(100, '处理完成!', translateData.cached ? '✓ 结果来自历史缓存' : '');

    } catch (error) {
        updateProgress(0, '处理失败', `✗ 错误: ${error.message}`);
        alert(`处理过程中出现错误: ${error.message}`);
        showSection('config');
    } finally {
        isProcessing = false;
    }
}

// 获取配置
function getConfig() {
    return {
        categories: Array.from(selectedCategories),
        keywords: document.getElementById('keywordSearch').value.trim(),
        timeRange: parseInt(document.getElementById('timeRange').value),
        maxPapers: parseInt(document.getElementById('maxPapers').value),
        concurrency: parseInt(document.getElementById('concurrency').value),
        llm: getLLMConfig()
    };
}

function getAgentConfig() {
    return {
        question: document.getElementById('agentQuestion').value.trim(),
        timeRange: parseInt(document.getElementById('agentTimeRange').value),
        maxPapers: parseInt(document.getElementById('agentMaxPapers').value),
        papersPerQuery: parseInt(document.getElementById('agentPapersPerQuery').value),
        llm: getLLMConfig()
    };
}

async function handleAgentStart() {
    if (isAgentProcessing) return;

    const config = getAgentConfig();
    if (!config.question) {
        alert('请输入研究问题');
        return;
    }
    if (!config.llm.endpoint || !config.llm.model) {
        alert('请先配置 LLM API');
        return;
    }

    isAgentProcessing = true;
    updateAgentButton();
    currentAgentResult = null;

    showSection('progress');
    document.getElementById('progressDetails').innerHTML = '';
    showAgentProgressPanel();
    startAgentProgressTicker();
    updateProgress(5, '研究 Agent 启动中...', '开始理解研究问题并生成搜索策略...');

    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 900000);

    try {
        setAgentProgressStep('intent');
        updateProgress(20, '正在规划多步检索...', '调用 LLM 生成意图识别和子查询...');
        const response = await fetch(`${API_BASE}/api/agent/research`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(config),
            signal: controller.signal
        });

        clearTimeout(timeoutId);

        if (!response.ok) {
            const errorData = await response.json().catch(() => ({}));
            throw new Error(errorData.error || '研究 Agent 请求失败');
        }

        const data = await response.json();
        if (!data.success) {
            throw new Error(data.error || '研究 Agent 运行失败');
        }

        stopAgentProgressTicker();
        setAgentProgressStep('render');
        updateProgress(85, '正在渲染研究报告...', data.cached ? '✓ 命中研究 Agent 缓存' : '✓ 已完成检索和综述生成');

        currentAgentResult = {
            question: data.question || config.question,
            intent: data.intent || {},
            strategy: data.strategy || {},
            subQueries: data.subQueries || [],
            sourcePriorities: data.sourcePriorities || [],
            papers: data.papers || [],
            synthesis: data.synthesis || {},
            evaluation: data.evaluation || {},
            trajectory: data.trajectory || [],
            stats: data.stats || {},
            cached: !!data.cached,
            hash: data.hash || '',
            cacheCreated: data.cacheCreated || ''
        };

        renderAgentResults(currentAgentResult, data.cached);
        markAgentProgressDone();
        updateProgress(100, '研究 Agent 完成!', data.cached ? '✓ 报告来自历史缓存' : '✓ 研究报告已生成');
        showSection('agentResults');
    } catch (error) {
        clearTimeout(timeoutId);
        stopAgentProgressTicker();
        markAgentProgressFailed();
        const message = error.name === 'AbortError'
            ? '请求超时（超过15分钟），请稍后重试或减少论文数'
            : error.message;
        updateProgress(0, '研究 Agent 失败', `✗ 错误: ${message}`);
        alert(`研究 Agent 运行失败: ${message}`);
        showSection('config');
    } finally {
        isAgentProcessing = false;
        updateAgentButton();
    }
}

function showAgentProgressPanel() {
    if (!agentProgressPanel) return;
    agentProgressPanel.classList.remove('hidden');
    setAgentProgressStep('intent');
}

function hideAgentProgressPanel() {
    if (!agentProgressPanel) return;
    agentProgressPanel.classList.add('hidden');
}

function setAgentProgressStep(stepKey) {
    if (!agentProgressPanel) return;

    const steps = Array.from(agentProgressPanel.querySelectorAll('.agent-progress-step'));
    const activeIndex = steps.findIndex(step => step.dataset.agentStep === stepKey);

    steps.forEach((step, index) => {
        step.classList.toggle('active', index === activeIndex);
        step.classList.toggle('done', activeIndex > -1 && index < activeIndex);
        step.classList.remove('failed');
    });

    const flowItem = agentProgressFlow.find(item => item.key === stepKey);
    if (flowItem && agentProgressNote) {
        agentProgressNote.textContent = flowItem.note;
    }
}

function startAgentProgressTicker() {
    stopAgentProgressTicker();
    let index = 0;
    setAgentProgressStep(agentProgressFlow[index].key);

    agentProgressTimer = setInterval(() => {
        index = Math.min(index + 1, agentProgressFlow.length - 2);
        const item = agentProgressFlow[index];
        setAgentProgressStep(item.key);
        updateProgress(item.percent, item.status);
    }, 4500);
}

function stopAgentProgressTicker() {
    if (!agentProgressTimer) return;
    clearInterval(agentProgressTimer);
    agentProgressTimer = null;
}

function markAgentProgressDone() {
    if (!agentProgressPanel) return;
    agentProgressPanel.querySelectorAll('.agent-progress-step').forEach(step => {
        step.classList.add('done');
        step.classList.remove('active', 'failed');
    });
    if (agentProgressNote) {
        agentProgressNote.textContent = '研究报告已生成。';
    }
}

function markAgentProgressFailed() {
    if (!agentProgressPanel) return;
    const active = agentProgressPanel.querySelector('.agent-progress-step.active');
    if (active) {
        active.classList.add('failed');
    }
    if (agentProgressNote) {
        agentProgressNote.textContent = '研究 Agent 运行失败，请查看错误信息后重试。';
    }
}

// 显示区域
function showSection(section) {
    configSection.classList.add('hidden');
    progressSection.classList.add('hidden');
    resultsSection.classList.add('hidden');
    agentWorkspaceSection.classList.add('hidden');
    agentResultsSection.classList.add('hidden');

    if (section === 'config') configSection.classList.remove('hidden');
    if (section === 'agentWorkspace') agentWorkspaceSection.classList.remove('hidden');
    if (section === 'progress') progressSection.classList.remove('hidden');
    if (section === 'results') resultsSection.classList.remove('hidden');
    if (section === 'agentResults') agentResultsSection.classList.remove('hidden');
}

// 更新进度
function updateProgress(percent, status, detail = '') {
    const progressBar = document.getElementById('progressBar');
    const progressStatus = document.getElementById('progressStatus');
    const progressDetails = document.getElementById('progressDetails');

    progressBar.style.width = `${percent}%`;
    progressStatus.textContent = status;

    if (detail) {
        const entry = document.createElement('div');
        entry.className = 'log-entry';
        if (detail.includes('✓')) entry.classList.add('success');
        if (detail.includes('✗')) entry.classList.add('error');
        entry.textContent = detail;
        progressDetails.appendChild(entry);
        progressDetails.scrollTop = progressDetails.scrollHeight;
    }
}

// 渲染结果
function renderResults(papers, config, stats, cached) {
    const container = document.getElementById('papersContainer');
    const meta = document.getElementById('resultsMeta');
    const statsPanel = document.getElementById('statsPanel');
    const resultsHeader = document.querySelector('.results-header');
    currentRunConfig = config;

    // 显示统计信息
    if (stats) {
        statsPanel.style.display = 'block';
        // 模型名称
        const modelName = config.llm.model || 'doubao-seed-2.0-lite';
        document.getElementById('statModel').textContent = modelName.length > 15 ? modelName.substring(0, 15) + '...' : modelName;
        // 总 Tokens
        document.getElementById('statTotalTokens').textContent = stats.totalTokens.toLocaleString();
        // 总耗时
        document.getElementById('statTotalTime').textContent = `${Math.round(stats.totalTime)}s`;
        // 平均每篇耗时
        document.getElementById('statAvgTime').textContent = `${stats.avgTimePerPaper.toFixed(1)}s`;
    } else {
        statsPanel.style.display = 'none';
    }

    // 元信息
    const categories = config.categories && config.categories.length > 0
        ? config.categories.slice(0, 5).join(', ') + (config.categories.length > 5 ? ` 等${config.categories.length}个分类` : '')
        : '未限定分类';
    const keywords = config.keywords ? escapeHtml(config.keywords) : '';
    const dateRange = `最近 ${config.timeRange} 天`;
    meta.innerHTML = `
        <strong>分类:</strong> ${escapeHtml(categories)} |
        ${keywords ? `<strong>关键词:</strong> ${keywords} |` : ''}
        <strong>时间范围:</strong> ${dateRange} |
        <strong>共 ${papers.length} 篇论文</strong> |
        <strong>并发数:</strong> ${config.concurrency}
        ${cached ? '<span class="cached-badge">📦 来自缓存</span>' : ''}
    `;

    renderTrendPanel(papers, config);

    // 清空容器
    container.innerHTML = '';

    // 渲染每篇论文
    papers.forEach((paper, index) => {
        const card = document.createElement('div');
        card.className = 'paper-card';

        const authors = paper.authors.slice(0, 5).join(', ') +
            (paper.authors.length > 5 ? ` 等 ${paper.authors.length} 位作者` : '');

        // 先转义 HTML，然后数学公式会在渲染后处理
        const highlightText = escapeHtml(paper.highlight || '');
        const abstractText = escapeHtml(paper.chineseAbstract || '');

        const highlightHtml = paper.highlight && !paper.translationError ? `
            <div class="paper-highlight">
                <div class="paper-highlight-label">✨ 一句话亮点</div>
                <div class="paper-highlight-text render-math">${highlightText}</div>
            </div>
        ` : '';

        const abstractHtml = paper.chineseAbstract ? `
            <div class="paper-abstract">
                <div class="paper-abstract-label">📝 中文摘要</div>
                <div class="render-math">${abstractText}</div>
            </div>
        ` : '';

        card.innerHTML = `
            <div class="paper-header">
                <div class="paper-title">
                    <a href="${paper.link}" target="_blank" rel="noopener">${escapeHtml(paper.title)}</a>
                </div>
                <div class="paper-authors">${escapeHtml(authors)}</div>
            </div>
            ${highlightHtml}
            ${abstractHtml}
            <div class="paper-footer">
                <div class="paper-meta">
                    📅 ${paper.published} | 🏷️ ${paper.primaryCategory}
                </div>
                <div class="paper-actions">
                    <button class="btn-intensive-read" onclick="doIntensiveRead(${index})" title="论文精读">
                        📖 精读
                    </button>
                    <a href="${paper.pdfLink || paper.link}" target="_blank" rel="noopener" class="paper-link">
                        📄 查看原文
                    </a>
                </div>
            </div>
        `;

        container.appendChild(card);
    });

    // 渲染数学公式
    renderMathInElement(container);
}

// ==================== 研究 Agent ====================

function asArray(value) {
    if (Array.isArray(value)) return value.filter(item => item !== null && item !== undefined && String(item).trim());
    if (value === null || value === undefined || value === '') return [];
    return [value];
}

function compactText(value, fallback = '') {
    if (Array.isArray(value)) return value.map(item => String(item)).join('、');
    if (value === null || value === undefined || value === '') return fallback;
    return String(value);
}

function renderAgentResults(result, cached) {
    const meta = document.getElementById('agentResultsMeta');
    const stats = result.stats || {};
    const cachedBadge = cached ? '<span class="cached-badge">📦 来自缓存</span>' : '';
    const tokenText = stats.totalTokens ? ` | <strong>Tokens:</strong> ${stats.totalTokens.toLocaleString()}` : '';
    meta.innerHTML = `
        <strong>问题:</strong> ${escapeHtml(result.question || '')} |
        <strong>子查询:</strong> ${stats.totalQueries || (result.subQueries || []).length} |
        <strong>论文:</strong> ${stats.finalPapers || (result.papers || []).length} |
        <strong>时间范围:</strong> 最近 ${stats.timeRange || '-'} 天${tokenText}
        ${cachedBadge}
    `;

    renderAgentIntent(result.intent || {});
    renderAgentSources(result.sourcePriorities || []);
    renderAgentStrategy(result.strategy || {}, result.subQueries || []);
    renderAgentSynthesis(result.synthesis || {});
    renderAgentEvaluation(result.evaluation || {});
    renderAgentTrajectory(result.trajectory || []);
    renderAgentPapers(result.papers || []);
    renderMathInElement(agentResultsSection);
}

function renderAgentIntent(intent) {
    const container = document.getElementById('agentIntent');
    const rows = [
        ['研究主题', intent.researchTopic],
        ['任务领域', intent.taskDomain],
        ['关键实体', intent.keyEntities],
        ['同义扩展', intent.synonyms],
        ['时间倾向', intent.timeSensitivity],
        ['纳入范围', intent.inScope],
        ['排除范围', intent.outOfScope]
    ];

    container.innerHTML = rows
        .filter(([, value]) => compactText(value).trim())
        .map(([label, value]) => `
            <div class="agent-kv-row">
                <span class="agent-kv-label">${escapeHtml(label)}</span>
                <span class="agent-kv-value render-math">${escapeHtml(compactText(value))}</span>
            </div>
        `).join('') || '<div class="trend-empty">暂无查询理解信息</div>';
}

function renderAgentSources(sources) {
    const container = document.getElementById('agentSources');
    if (!sources.length) {
        container.innerHTML = '<div class="trend-empty">暂无数据源信息</div>';
        return;
    }

    container.innerHTML = sources.map(source => `
        <div class="agent-source-row">
            <div>
                <strong>${escapeHtml(source.name || '')}</strong>
                <span class="agent-badge">${escapeHtml(source.status || '')}</span>
            </div>
            <div class="agent-muted render-math">${escapeHtml(source.reason || '')}</div>
        </div>
    `).join('');
}

function renderAgentStrategy(strategy, subQueries) {
    const strategyContainer = document.getElementById('agentStrategy');
    const queryContainer = document.getElementById('agentSubQueries');
    const angles = asArray(strategy.searchAngles);
    strategyContainer.innerHTML = `
        <p class="agent-overview render-math">${escapeHtml(strategy.overview || '已生成多角度 arXiv 检索策略。')}</p>
        ${angles.length ? `<div class="agent-chip-row">${angles.map(angle => `<span class="agent-chip">${escapeHtml(angle)}</span>`).join('')}</div>` : ''}
    `;

    if (!subQueries.length) {
        queryContainer.innerHTML = '<div class="trend-empty">暂无子查询</div>';
        return;
    }

    queryContainer.innerHTML = subQueries.map(query => {
        const categories = asArray(query.arxivCategories).join(', ');
        const error = query.error ? `<div class="agent-error">检索失败: ${escapeHtml(query.error)}</div>` : '';
        const relaxed = query.relaxedQuery ? `
            <div class="agent-relaxed-query">
                <strong>宽松重试:</strong> ${escapeHtml(query.relaxedQuery)}
                ${query.relaxationNote ? `<span>${escapeHtml(query.relaxationNote)}</span>` : ''}
            </div>
        ` : '';
        return `
            <div class="agent-query-item">
                <div class="agent-query-head">
                    <span class="agent-query-id">${escapeHtml(query.id || '')}</span>
                    <strong>${escapeHtml(query.angle || '')}</strong>
                    <span class="agent-badge">${Number(query.resultCount || 0)} 篇</span>
                </div>
                <div class="agent-query-text">${escapeHtml(query.query || '')}</div>
                ${relaxed}
                <div class="agent-muted">分类: ${escapeHtml(categories || '默认')}</div>
                ${query.rationale ? `<div class="agent-muted render-math">${escapeHtml(query.rationale)}</div>` : ''}
                ${error}
            </div>
        `;
    }).join('');
}

function renderEvidence(evidence) {
    const items = asArray(evidence);
    if (!items.length) return '';
    return `<div class="agent-evidence">证据: ${items.map(item => `<span>#${escapeHtml(item)}</span>`).join(' ')}</div>`;
}

function renderAgentSynthesis(synthesis) {
    const container = document.getElementById('agentSynthesis');
    const parts = [];

    parts.push(`<div class="agent-overview render-math">${escapeHtml(synthesis.overview || '暂无综述内容')}</div>`);
    parts.push(renderAgentInsightGroup('关键趋势', synthesis.keyTrends || [], item => `
        <div class="agent-insight-title">${escapeHtml(item.label || '')}</div>
        <div class="render-math">${escapeHtml(item.summary || '')}</div>
        ${renderEvidence(item.evidence)}
    `));
    parts.push(renderAgentInsightGroup('方法路线', synthesis.methodMap || [], item => `
        <div class="agent-insight-title">${escapeHtml(item.method || '')}</div>
        <div class="render-math">${escapeHtml(item.description || '')}</div>
        ${renderEvidence(item.evidence)}
    `));
    parts.push(renderAgentInsightGroup('代表性论文判断', synthesis.representativePapers || [], item => `
        <div class="agent-insight-title">#${escapeHtml(item.index || '')} ${escapeHtml(item.title || '')}</div>
        <div class="render-math">${escapeHtml(item.reason || '')}</div>
    `));
    parts.push(renderAgentTextList('局限与证据边界', synthesis.limitations || []));
    parts.push(renderAgentTextList('后续方向', synthesis.futureDirections || []));

    container.innerHTML = parts.join('');
}

function renderAgentEvaluation(evaluation) {
    const container = document.getElementById('agentEvaluation');
    if (!container) return;

    if (!evaluation || Object.keys(evaluation).length === 0) {
        container.innerHTML = '<div class="trend-empty">暂无质量评估信息</div>';
        return;
    }

    const score = Number(evaluation.score || 0);
    const level = evaluation.coverageLevel || 'limited';
    const issues = asArray(evaluation.issues);
    const recommendations = asArray(evaluation.recommendations);

    container.innerHTML = `
        <div class="agent-evaluation-head">
            <div class="agent-score agent-score-${escapeHtml(level)}">${score}</div>
            <div>
                <div class="agent-evaluation-title">${escapeHtml(evaluation.coverageLabel || '覆盖评估')}</div>
                <div class="agent-muted">
                    证据论文 ${Number(evaluation.evidencePaperCount || 0)} 篇，
                    空查询 ${Number(evaluation.zeroResultQueryCount || 0)} 个，
                    宽松重试 ${Number(evaluation.relaxedQueryCount || 0)} 次
                </div>
            </div>
        </div>
        ${issues.length ? `
            <div class="agent-subsection">
                <h4>风险提示</h4>
                <ul class="agent-text-list">${issues.map(item => `<li>${escapeHtml(item)}</li>`).join('')}</ul>
            </div>
        ` : ''}
        ${recommendations.length ? `
            <div class="agent-subsection">
                <h4>改进建议</h4>
                <ul class="agent-text-list">${recommendations.map(item => `<li>${escapeHtml(item)}</li>`).join('')}</ul>
            </div>
        ` : ''}
    `;
}

function renderAgentTrajectory(trajectory) {
    const container = document.getElementById('agentTrajectory');
    if (!container) return;

    const steps = asArray(trajectory);
    if (!steps.length) {
        container.innerHTML = '<div class="trend-empty">暂无执行轨迹</div>';
        return;
    }

    container.innerHTML = steps.map(step => {
        const input = step.input && typeof step.input === 'object'
            ? compactTrajectoryInput(step.input)
            : compactText(step.input);
        const output = compactText(step.outputSummary);
        return `
            <div class="agent-trajectory-item agent-trajectory-${escapeHtml(step.status || 'success')}">
                <div class="agent-trajectory-index">${Number(step.index || 0)}</div>
                <div class="agent-trajectory-body">
                    <div class="agent-trajectory-head">
                        <strong>${escapeHtml(step.title || '')}</strong>
                        <span>${escapeHtml(step.type || 'step')}</span>
                    </div>
                    ${step.content ? `<div class="agent-muted render-math">${escapeHtml(step.content)}</div>` : ''}
                    ${step.tool ? `<div class="agent-trajectory-tool">Tool: ${escapeHtml(step.tool)}</div>` : ''}
                    ${input ? `<div class="agent-trajectory-io">输入：${escapeHtml(input)}</div>` : ''}
                    ${output ? `<div class="agent-trajectory-io">输出：${escapeHtml(output)}</div>` : ''}
                </div>
            </div>
        `;
    }).join('');
}

function compactTrajectoryInput(input) {
    const parts = [];
    if (input.queryId) parts.push(input.queryId);
    if (input.angle) parts.push(input.angle);
    if (input.query) parts.push(input.query);
    if (input.categories) parts.push(`分类 ${asArray(input.categories).join(', ')}`);
    if (input.timeRange) parts.push(`最近 ${input.timeRange} 天`);
    return parts.join(' | ');
}

function renderAgentInsightGroup(title, items, renderItem) {
    if (!items || items.length === 0) return '';
    return `
        <div class="agent-subsection">
            <h4>${escapeHtml(title)}</h4>
            <div class="agent-insight-list">
                ${items.map(item => `<div class="agent-insight-item">${renderItem(item)}</div>`).join('')}
            </div>
        </div>
    `;
}

function renderAgentTextList(title, items) {
    const values = asArray(items);
    if (!values.length) return '';
    return `
        <div class="agent-subsection">
            <h4>${escapeHtml(title)}</h4>
            <ul class="agent-text-list">
                ${values.map(item => `<li class="render-math">${escapeHtml(item)}</li>`).join('')}
            </ul>
        </div>
    `;
}

function renderAgentPapers(papers) {
    const container = document.getElementById('agentPapers');
    if (!papers.length) {
        container.innerHTML = '<div class="trend-empty">当前策略未检索到论文</div>';
        return;
    }

    container.innerHTML = papers.map((paper, index) => {
        const authors = asArray(paper.authors).slice(0, 5).join(', ');
        const angles = asArray(paper.matchAngles).join('、');
        const score = Number(paper.relevanceScore || 0).toFixed(2);
        return `
            <div class="agent-paper-item">
                <div class="agent-paper-index">#${index + 1}</div>
                <div class="agent-paper-body">
                    <div class="agent-paper-title">
                        <a href="${paper.link || paper.pdfLink || '#'}" target="_blank" rel="noopener">${escapeHtml(paper.title || '')}</a>
                    </div>
                    <div class="agent-muted">${escapeHtml(authors)}</div>
                    <div class="agent-muted">📅 ${escapeHtml(paper.published || '')} | 🏷️ ${escapeHtml(paper.primaryCategory || '')} | 相关度 ${score}</div>
                    ${angles ? `<div class="agent-chip-row">${asArray(paper.matchAngles).map(angle => `<span class="agent-chip">${escapeHtml(angle)}</span>`).join('')}</div>` : ''}
                    <div class="agent-paper-abstract render-math">${escapeHtml(paper.abstract || '')}</div>
                </div>
            </div>
        `;
    }).join('');
}

function exportAgentMarkdown() {
    if (!currentAgentResult) return;

    const result = currentAgentResult;
    const date = new Date().toLocaleDateString('zh-CN');
    const stats = result.stats || {};
    let markdown = `# 🧭 PaperExpress 研究 Agent 报告\n\n`;
    markdown += `**生成日期:** ${date}  \n`;
    markdown += `**研究问题:** ${result.question || ''}  \n`;
    markdown += `**时间范围:** 最近 ${stats.timeRange || '-'} 天  \n`;
    markdown += `**论文数量:** ${(result.papers || []).length} 篇\n\n`;

    markdown += `## 查询理解\n\n`;
    const intent = result.intent || {};
    markdown += `- 研究主题：${compactText(intent.researchTopic)}\n`;
    markdown += `- 任务领域：${compactText(intent.taskDomain)}\n`;
    markdown += `- 关键实体：${compactText(intent.keyEntities)}\n`;
    markdown += `- 同义扩展：${compactText(intent.synonyms)}\n\n`;

    markdown += `## 多步搜索策略\n\n`;
    markdown += `${(result.strategy || {}).overview || ''}\n\n`;
    (result.subQueries || []).forEach(query => {
        markdown += `- **${query.id} ${query.angle}:** ${query.query} (${query.resultCount || 0} 篇)\n`;
    });
    markdown += `\n`;

    markdown += `## 智能综述\n\n`;
    const synthesis = result.synthesis || {};
    markdown += `${synthesis.overview || ''}\n\n`;
    markdown += markdownAgentItems('关键趋势', synthesis.keyTrends || [], item =>
        `- **${item.label || ''}:** ${item.summary || ''}${markdownEvidence(item.evidence)}`
    );
    markdown += markdownAgentItems('方法路线', synthesis.methodMap || [], item =>
        `- **${item.method || ''}:** ${item.description || ''}${markdownEvidence(item.evidence)}`
    );
    markdown += markdownAgentItems('局限与证据边界', synthesis.limitations || [], item => `- ${item}`);
    markdown += markdownAgentItems('后续方向', synthesis.futureDirections || [], item => `- ${item}`);

    const evaluation = result.evaluation || {};
    if (Object.keys(evaluation).length) {
        markdown += `## 质量评估\n\n`;
        markdown += `- 评分：${evaluation.score || 0}/100\n`;
        markdown += `- 覆盖状态：${evaluation.coverageLabel || evaluation.coverageLevel || ''}\n`;
        markdown += `- 证据论文：${evaluation.evidencePaperCount || 0} 篇\n`;
        markdown += `- 空查询：${evaluation.zeroResultQueryCount || 0} 个\n`;
        markdown += `- 宽松重试：${evaluation.relaxedQueryCount || 0} 次\n\n`;
        markdown += markdownAgentItems('质量风险', evaluation.issues || [], item => `- ${item}`);
        markdown += markdownAgentItems('优化建议', evaluation.recommendations || [], item => `- ${item}`);
    }

    const trajectory = result.trajectory || [];
    if (trajectory.length) {
        markdown += `## 执行轨迹\n\n`;
        trajectory.forEach(step => {
            markdown += `- **${step.index || ''}. ${step.title || ''}** [${step.type || 'step'} / ${step.status || 'success'}]`;
            if (step.tool) markdown += ` Tool: ${step.tool}`;
            if (step.outputSummary) markdown += `，${step.outputSummary}`;
            markdown += `\n`;
        });
        markdown += `\n`;
    }

    markdown += `## 代表论文\n\n`;
    (result.papers || []).forEach((paper, index) => {
        markdown += `### ${index + 1}. ${paper.title || ''}\n\n`;
        markdown += `- 作者：${asArray(paper.authors).join(', ')}  \n`;
        markdown += `- 发布日期：${paper.published || ''}  \n`;
        markdown += `- 分类：${paper.primaryCategory || ''}  \n`;
        markdown += `- 链接：${paper.link || paper.pdfLink || ''}  \n`;
        markdown += `- 命中角度：${asArray(paper.matchAngles).join('、')}\n\n`;
        markdown += `${paper.abstract || ''}\n\n`;
    });

    markdown += `\n*Generated by PaperExpress Research Agent*\n`;

    const blob = new Blob([markdown], { type: 'text/markdown;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `ResearchAgent_${date.replace(/\//g, '-')}.md`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
}

function markdownEvidence(evidence) {
    const items = asArray(evidence);
    return items.length ? `（证据: ${items.map(item => `#${item}`).join(', ')}）` : '';
}

function markdownAgentItems(title, items, renderItem) {
    const values = asArray(items);
    if (!values.length) return '';
    return `## ${title}\n\n${values.map(renderItem).join('\n')}\n\n`;
}

// 渲染数学公式（避免无限递归）
function renderMathInElement(element) {
    if (typeof katex === 'undefined') return;

    try {
        // 只渲染标记为 .render-math 的元素
        const mathElements = element.querySelectorAll('.render-math');
        mathElements.forEach(el => {
            const text = el.textContent;
            const tokenRegex = /\$([^$]+)\$|\*\*([^*]+)\*\*|`([^`]+)`/g;
            const fragment = document.createDocumentFragment();
            let lastIndex = 0;
            let changed = false;
            let match;

            while ((match = tokenRegex.exec(text)) !== null) {
                if (match.index > lastIndex) {
                    fragment.appendChild(document.createTextNode(text.slice(lastIndex, match.index)));
                }

                if (match[1]) {
                    const formula = match[1];
                    if (formula.length <= 200) {
                        const wrapper = document.createElement('span');
                        try {
                            wrapper.innerHTML = katex.renderToString(formula, {
                                throwOnError: false,
                                displayMode: false
                            });
                        } catch (e) {
                            wrapper.textContent = match[0];
                        }
                        fragment.appendChild(wrapper);
                        changed = true;
                    } else {
                        fragment.appendChild(document.createTextNode(match[0]));
                    }
                } else if (match[2]) {
                    const strong = document.createElement('strong');
                    strong.textContent = match[2];
                    fragment.appendChild(strong);
                    changed = true;
                } else if (match[3]) {
                    const code = document.createElement('code');
                    code.textContent = match[3];
                    fragment.appendChild(code);
                    changed = true;
                }

                lastIndex = tokenRegex.lastIndex;
            }

            if (changed) {
                if (lastIndex < text.length) {
                    fragment.appendChild(document.createTextNode(text.slice(lastIndex)));
                }
                el.replaceChildren(fragment);
            }
        });
    } catch (e) {
        console.warn('Rendering skipped:', e.message);
    }
}

// ==================== 当前结果趋势统计 ====================

function renderTrendPanel(papers, config) {
    const panel = document.getElementById('trendPanel');
    if (!panel) return;

    if (!papers || papers.length === 0) {
        panel.style.display = 'none';
        return;
    }

    panel.style.display = 'block';
    document.getElementById('trendScope').textContent = `基于当前 ${papers.length} 篇论文统计`;

    const categoryItems = getCountItems(papers.map(paper => paper.primaryCategory || '未分类'), 8);
    const dateItems = getCountItems(papers.map(paper => paper.published || '未知日期'), 8, true);

    renderTrendLoading('trendTopics', 'LLM 正在生成 Topic...');
    renderTrendLoading('trendMethods', 'LLM 正在生成方法词...');
    renderTrendList('trendCategories', categoryItems, papers.length, '暂无分类数据');
    renderTrendList('trendDates', dateItems, papers.length, '暂无日期数据');

    loadLlmTrendSummary(papers, config);
}

async function loadLlmTrendSummary(papers, config) {
    const requestId = ++trendRequestSeq;
    const llmConfig = getTrendLLMConfig(config);

    if (!llmConfig.endpoint || !llmConfig.model) {
        renderTrendNotice('trendTopics', '请先配置 LLM 后生成 Topic');
        renderTrendNotice('trendMethods', '请先配置 LLM 后生成方法词');
        return;
    }

    try {
        const response = await fetch(`${API_BASE}/api/trend_summary`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                papers: papers.map(paper => ({
                    title: paper.title,
                    abstract: paper.abstract,
                    highlight: paper.highlight,
                    chineseAbstract: paper.chineseAbstract,
                    primaryCategory: paper.primaryCategory,
                    published: paper.published
                })),
                llm: llmConfig
            })
        });

        if (requestId !== trendRequestSeq) return;

        if (!response.ok) {
            const errorData = await response.json().catch(() => ({}));
            throw new Error(errorData.error || '趋势摘要请求失败');
        }

        const data = await response.json();
        if (!data.success) {
            throw new Error(data.error || '趋势摘要生成失败');
        }

        const result = data.result || {};
        renderTrendList('trendTopics', result.topics || [], papers.length, '暂无明显 Topic');
        renderTrendList('trendMethods', result.methods || [], papers.length, '暂无明显方法词');

        const tokenText = result.totalTokens ? ` · 趋势摘要 ${result.totalTokens.toLocaleString()} tokens` : '';
        const warningText = result.warning ? ` · ${result.warning}` : '';
        document.getElementById('trendScope').textContent = `基于当前 ${papers.length} 篇论文统计${tokenText}${warningText}`;
    } catch (error) {
        if (requestId !== trendRequestSeq) return;
        renderTrendNotice('trendTopics', `Topic 生成失败: ${error.message}`);
        renderTrendNotice('trendMethods', `方法词生成失败: ${error.message}`);
    }
}

function getTrendLLMConfig(config) {
    const formConfig = getLLMConfig();
    const configLlm = config.llm || {};
    const hasFullConfig = configLlm.endpoint && configLlm.key && configLlm.model;
    if (hasFullConfig) {
        return configLlm;
    }

    return {
        endpoint: formConfig.endpoint || configLlm.endpoint || '',
        key: formConfig.key || configLlm.key || '',
        model: formConfig.model || configLlm.model || ''
    };
}

function getCountItems(values, limit, sortByLabel = false) {
    const counts = new Map();
    values.filter(Boolean).forEach(value => counts.set(value, (counts.get(value) || 0) + 1));
    const items = Array.from(counts.entries()).map(([label, count]) => ({ label, count }));
    if (sortByLabel) {
        items.sort((a, b) => b.label.localeCompare(a.label));
    } else {
        items.sort((a, b) => b.count - a.count || a.label.localeCompare(b.label));
    }
    return items.slice(0, limit);
}

function renderTrendLoading(containerId, text) {
    const container = document.getElementById(containerId);
    if (!container) return;
    container.replaceChildren();

    const loading = document.createElement('div');
    loading.className = 'trend-empty';
    loading.textContent = text;
    container.appendChild(loading);
}

function renderTrendNotice(containerId, text) {
    const container = document.getElementById(containerId);
    if (!container) return;
    container.replaceChildren();

    const notice = document.createElement('div');
    notice.className = 'trend-empty';
    notice.textContent = text;
    container.appendChild(notice);
}

function renderTrendList(containerId, items, total, emptyText) {
    const container = document.getElementById(containerId);
    if (!container) return;

    container.replaceChildren();
    if (!items || items.length === 0) {
        const empty = document.createElement('div');
        empty.className = 'trend-empty';
        empty.textContent = emptyText;
        container.appendChild(empty);
        return;
    }

    const maxCount = Math.max(...items.map(item => item.count), 1);
    items.forEach(item => {
        const row = document.createElement('div');
        row.className = 'trend-bar-row';

        const top = document.createElement('div');
        top.className = 'trend-bar-top';

        const label = document.createElement('span');
        label.className = 'trend-label';
        label.textContent = item.label;

        const count = document.createElement('span');
        count.className = 'trend-count';
        count.textContent = `${item.count} 篇`;

        const track = document.createElement('div');
        track.className = 'trend-bar-track';

        const fill = document.createElement('div');
        fill.className = 'trend-bar-fill';
        fill.style.width = `${Math.max(8, Math.round((item.count / maxCount) * 100))}%`;

        const ratio = document.createElement('span');
        ratio.className = 'trend-ratio';
        ratio.textContent = `${Math.round((item.count / total) * 100)}%`;

        top.appendChild(label);
        top.appendChild(count);
        track.appendChild(fill);
        row.appendChild(top);
        row.appendChild(track);
        row.appendChild(ratio);
        if (item.reason) {
            const reason = document.createElement('div');
            reason.className = 'trend-reason';
            reason.textContent = item.reason;
            row.appendChild(reason);
        }
        container.appendChild(row);
    });
}

// 导出为 Markdown
function exportToMarkdown() {
    if (currentPapers.length === 0) return;

    const date = new Date().toLocaleDateString('zh-CN');
    let markdown = `# 📄 PaperExpress 论文速递\n\n`;
    markdown += `**生成日期:** ${date}  \n`;
    markdown += `**论文数量:** ${currentPapers.length} 篇\n\n`;
    if (currentRunConfig) {
        const categoryText = currentRunConfig.categories && currentRunConfig.categories.length > 0
            ? currentRunConfig.categories.join(', ')
            : '未限定分类';
        markdown += `**分类:** ${categoryText}  \n`;
        if (currentRunConfig.keywords) {
            markdown += `**关键词:** ${currentRunConfig.keywords}  \n`;
        }
        markdown += `**时间范围:** 最近 ${currentRunConfig.timeRange} 天\n\n`;
    }
    markdown += `---\n\n`;

    currentPapers.forEach((paper, index) => {
        markdown += `## ${index + 1}. ${paper.title}\n\n`;
        markdown += `- **作者:** ${paper.authors.join(', ')}  \n`;
        markdown += `- **发布日期:** ${paper.published}  \n`;
        markdown += `- **分类:** ${paper.primaryCategory}  \n`;
        markdown += `- **链接:** [arXiv](${paper.link})\n\n`;

        if (paper.highlight && !paper.translationError) {
            markdown += `**✨ 一句话亮点:** ${paper.highlight}\n\n`;
        }

        if (paper.chineseAbstract && !paper.translationError) {
            markdown += `**📝 中文摘要:**\n\n${paper.chineseAbstract}\n\n`;
        }

        markdown += `---\n\n`;
    });

    markdown += `\n*Generated by [PaperExpress](https://github.com)*\n`;

    // 下载文件
    const blob = new Blob([markdown], { type: 'text/markdown;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `PaperExpress_${date.replace(/\//g, '-')}.md`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
}


// HTML 转义
function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// ==================== 论文精读功能 ====================

let currentIntensiveReadPaper = null;

function doIntensiveRead(index) {
    const paper = currentPapers[index];
    if (!paper) return;

    currentIntensiveReadPaper = paper;

    // 更新标题
    document.getElementById('modalTitle').textContent = `📖 精读: ${paper.title.substring(0, 50)}${paper.title.length > 50 ? '...' : ''}`;

    // 显示弹窗
    showIntensiveReadModal();

    // 调用后端API进行精读
    intensiveReadPaper(paper);
}

function showIntensiveReadModal() {
    const modal = document.getElementById('intensiveReadModal');
    const loading = document.getElementById('modalLoading');
    const content = document.getElementById('modalContent');
    const error = document.getElementById('modalError');
    const processing = document.getElementById('modalProcessing');

    modal.classList.remove('hidden');
    loading.classList.remove('hidden');
    content.classList.add('hidden');
    error.classList.add('hidden');
    if (processing) processing.classList.remove('hidden');

    // 禁止背景滚动
    document.body.style.overflow = 'hidden';
}

function closeIntensiveReadModal() {
    const modal = document.getElementById('intensiveReadModal');
    const processing = document.getElementById('modalProcessing');

    modal.classList.add('hidden');
    if (processing) processing.classList.add('hidden');

    // 恢复背景滚动
    document.body.style.overflow = '';

    currentIntensiveReadPaper = null;
}

async function intensiveReadPaper(paper) {
    const config = getLLMConfig();

    if (!config.endpoint || !config.model) {
        showIntensiveReadError('请先配置 LLM API');
        return;
    }

    // 显示进度提示
    updateLoadingProgress('正在连接 LLM API...');

    // 创建 AbortController 用于超时控制
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 600000); // 10分钟超时

    try {
        const response = await fetch(`${API_BASE}/api/intensive_read`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                paper: paper,
                llm: config
            }),
            signal: controller.signal
        });

        clearTimeout(timeoutId);

        if (!response.ok) {
            const errorData = await response.json();
            throw new Error(errorData.error || '精读请求失败');
        }

        const data = await response.json();

        if (!data.success) {
            throw new Error(data.error || '精读分析失败');
        }

        // 渲染精读结果
        renderIntensiveReadResult(data.result, paper);

    } catch (error) {
        clearTimeout(timeoutId);
        if (error.name === 'AbortError') {
            showIntensiveReadError('请求超时（超过10分钟），请稍后重试或尝试更短的摘要');
        } else {
            showIntensiveReadError(error.message);
        }
    }
}

function updateLoadingProgress(message) {
    const loading = document.getElementById('modalLoading');
    const p = loading.querySelector('p');
    if (p) {
        p.textContent = message;
    }
}

function showIntensiveReadError(message) {
    const loading = document.getElementById('modalLoading');
    const error = document.getElementById('modalError');
    const content = document.getElementById('modalContent');
    const processing = document.getElementById('modalProcessing');

    loading.classList.add('hidden');
    if (processing) processing.classList.add('hidden');
    content.classList.add('hidden');
    error.classList.remove('hidden');
    error.textContent = `❌ ${message}`;
}

function renderIntensiveReadResult(result, paper) {
    const loading = document.getElementById('modalLoading');
    const content = document.getElementById('modalContent');
    const processing = document.getElementById('modalProcessing');

    loading.classList.add('hidden');
    if (processing) processing.classList.add('hidden');
    content.classList.remove('hidden');

    // 解析并渲染Markdown格式的精读内容
    content.innerHTML = parseIntensiveReadMarkdown(result.content);

    // 渲染数学公式
    renderMathInElement(content);

    // 保存原始内容用于导出
    content.dataset.rawContent = result.content;
}

function parseIntensiveReadMarkdown(text) {
    if (!text) return '<p>暂无内容</p>';

    const lines = text.replace(/\r\n/g, '\n').split('\n');
    const html = [];
    let listOpen = false;

    const closeList = () => {
        if (listOpen) {
            html.push('</ul>');
            listOpen = false;
        }
    };

    lines.forEach(line => {
        const trimmed = line.trim();
        if (!trimmed) {
            closeList();
            return;
        }

        const headingMatch = trimmed.match(/^#{2,4}\s+(.+)$/);
        if (headingMatch) {
            closeList();
            html.push(`<h4 class="render-math">${escapeHtml(headingMatch[1])}</h4>`);
            return;
        }

        const listMatch = trimmed.match(/^(\d+\.|[-*])\s+(.+)$/);
        if (listMatch) {
            if (!listOpen) {
                html.push('<ul>');
                listOpen = true;
            }
            html.push(`<li class="render-math">${escapeHtml(listMatch[2])}</li>`);
            return;
        }

        closeList();
        html.push(`<p class="render-math">${escapeHtml(trimmed)}</p>`);
    });

    closeList();
    return html.join('');
}

function exportIntensiveRead() {
    const content = document.getElementById('modalContent');
    const rawContent = content.dataset.rawContent;

    if (!rawContent || !currentIntensiveReadPaper) return;

    const paper = currentIntensiveReadPaper;
    const date = new Date().toLocaleDateString('zh-CN');

    let markdown = `# 📖 论文精读报告\n\n`;
    markdown += `**论文标题:** ${paper.title}\n`;
    markdown += `**作者:** ${paper.authors.join(', ')}\n`;
    markdown += `**发布日期:** ${paper.published}\n`;
    markdown += `**arXiv ID:** ${paper.id}\n`;
    markdown += `**生成日期:** ${date}\n\n`;
    markdown += `---\n\n`;
    markdown += rawContent;
    markdown += `\n\n---\n\n`;
    markdown += `*Generated by [PaperExpress](https://github.com)*\n`;

    // 下载文件
    const blob = new Blob([markdown], { type: 'text/markdown;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `精读_${paper.title.substring(0, 30).replace(/[\/\\:*?"<>|]/g, '_')}_${date.replace(/\//g, '-')}.md`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
}

// 点击遮罩关闭弹窗
document.addEventListener('click', (e) => {
    const modal = document.getElementById('intensiveReadModal');
    if (e.target === modal) {
        closeIntensiveReadModal();
    }
});

// ESC键关闭弹窗
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
        closeIntensiveReadModal();
        closeHistoryModal();
    }
});

// 点击遮罩关闭历史弹窗
document.addEventListener('click', (e) => {
    const historyModal = document.getElementById('historyModal');
    if (e.target === historyModal) {
        closeHistoryModal();
    }
});

// ==================== 历史记录功能 ====================

async function openHistoryModal() {
    const modal = document.getElementById('historyModal');
    const loading = document.getElementById('historyLoading');
    const content = document.getElementById('historyContent');

    modal.classList.remove('hidden');
    loading.classList.remove('hidden');
    content.classList.add('hidden');
    document.body.style.overflow = 'hidden';

    try {
        const response = await fetch(`${API_BASE}/api/history/list`);
        const data = await response.json();

        loading.classList.add('hidden');
        content.classList.remove('hidden');

        if (data.success) {
            renderHistoryContent(content, data);
        } else {
            content.innerHTML = `<div class="modal-error">加载失败: ${escapeHtml(data.error || '未知错误')}</div>`;
        }
    } catch (error) {
        loading.classList.add('hidden');
        content.classList.remove('hidden');
        content.innerHTML = `<div class="modal-error">❌ 加载历史记录失败: ${escapeHtml(error.message)}</div>`;
    }
}

function closeHistoryModal() {
    const modal = document.getElementById('historyModal');
    modal.classList.add('hidden');
    document.body.style.overflow = '';
}

function renderHistoryContent(container, data) {
    const papers = data.papers || {};
    const intensive = data.intensive || {};
    const agent = data.agent || {};

    const papersKeys = Object.keys(papers);
    const intensiveKeys = Object.keys(intensive);
    const agentKeys = Object.keys(agent);

    if (papersKeys.length === 0 && intensiveKeys.length === 0 && agentKeys.length === 0) {
        container.innerHTML = `
            <div class="history-empty">
                <p>暂无历史记录</p>
                <p class="history-empty-hint">执行论文速递、研究 Agent 或精读后，结果将自动保存</p>
            </div>
        `;
        return;
    }

    let html = '<div class="history-section">';

    // 论文速递记录
    if (papersKeys.length > 0) {
        html += '<h4>📰 论文速递记录</h4>';
        html += '<div class="history-list">';

        // 按时间倒序排列
        papersKeys.sort((a, b) => {
            const dateA = papers[a].created || '';
            const dateB = papers[b].created || '';
            return dateB.localeCompare(dateA);
        });

        papersKeys.forEach(key => {
            const record = papers[key];
            const date = new Date(record.created).toLocaleString('zh-CN');
            const count = record.count || 0;
            const keywordMeta = record.keywords ? ` · 关键词: ${escapeHtml(record.keywords)}` : '';
            html += `
                <div class="history-item">
                    <div class="history-item-info">
                        <div class="history-item-title">${escapeHtml(record.title || '未命名')}</div>
                        <div class="history-item-meta">${date} · ${count} 篇论文${keywordMeta}</div>
                    </div>
                    <div class="history-item-actions">
                        <button class="btn btn-small" onclick="loadPapersHistory('${key}')">加载</button>
                        <button class="btn btn-secondary btn-small" onclick="deletePapersHistory('${key}')">删除</button>
                    </div>
                </div>
            `;
        });

        html += '</div>';
    }

    // 研究 Agent 记录
    if (agentKeys.length > 0) {
        html += '<h4>🧭 研究 Agent 记录</h4>';
        html += '<div class="history-list">';

        agentKeys.sort((a, b) => {
            const dateA = agent[a].created || '';
            const dateB = agent[b].created || '';
            return dateB.localeCompare(dateA);
        });

        agentKeys.forEach(key => {
            const record = agent[key];
            const date = new Date(record.created).toLocaleString('zh-CN');
            const count = record.count || 0;
            html += `
                <div class="history-item">
                    <div class="history-item-info">
                        <div class="history-item-title">${escapeHtml(record.title || '研究 Agent')}</div>
                        <div class="history-item-meta">${date} · ${count} 篇论文 · ${escapeHtml(record.question || '')}</div>
                    </div>
                    <div class="history-item-actions">
                        <button class="btn btn-small" onclick="loadAgentHistory('${key}')">加载</button>
                        <button class="btn btn-secondary btn-small" onclick="deleteAgentHistory('${key}')">删除</button>
                    </div>
                </div>
            `;
        });

        html += '</div>';
    }

    // 精读记录
    if (intensiveKeys.length > 0) {
        html += '<h4>📖 精读记录</h4>';
        html += '<div class="history-list">';

        // 按时间倒序排列
        intensiveKeys.sort((a, b) => {
            const dateA = intensive[a].created || '';
            const dateB = intensive[b].created || '';
            return dateB.localeCompare(dateA);
        });

        intensiveKeys.forEach(key => {
            const record = intensive[key];
            const date = new Date(record.created).toLocaleString('zh-CN');
            html += `
                <div class="history-item">
                    <div class="history-item-info">
                        <div class="history-item-title">${escapeHtml(record.paperTitle || '未命名')}</div>
                        <div class="history-item-meta">${date}</div>
                    </div>
                    <div class="history-item-actions">
                        <button class="btn btn-small" onclick="loadIntensiveHistory('${key}')">查看</button>
                        <button class="btn btn-secondary btn-small" onclick="deleteIntensiveHistory('${key}')">删除</button>
                    </div>
                </div>
            `;
        });

        html += '</div>';
    }

    html += '</div>';
    container.innerHTML = html;
}

async function loadPapersHistory(hash) {
    try {
        const response = await fetch(`${API_BASE}/api/history/papers/${hash}`);
        const data = await response.json();

        if (!data.success || !data.record) {
            alert('加载失败: ' + (data.error || '记录不存在'));
            return;
        }

        const record = data.record;

        // 恢复论文列表
        const papers = record.papers || [];
        const results = record.results || [];

        const translatedPapers = papers.map((paper, i) => {
            const result = results[i] || { result: {} };
            const hasError = !result.success;
            return {
                ...paper,
                chineseAbstract: result.result.chineseAbstract || '解析失败',
                highlight: result.result.highlight || '解析失败',
                translationError: hasError,
                tokenUsage: {
                    promptTokens: result.result.promptTokens || 0,
                    completionTokens: result.result.completionTokens || 0,
                    totalTokens: result.result.totalTokens || 0
                }
            };
        });

        // 恢复配置
        const config = {
            categories: record.categories || [],
            keywords: record.keywords || '',
            timeRange: record.timeRange || 3,
            maxPapers: record.maxPapers || 20,
            concurrency: 3,
            llm: { model: record.model || '' }
        };

        // 恢复选中分类
        selectedCategories = new Set(record.categories || []);
        document.getElementById('keywordSearch').value = record.keywords || '';
        updateSelectedCategoriesDisplay();
        updateStartButton();

        currentPapers = translatedPapers;
        translationStats = record.stats || {};

        closeHistoryModal();
        renderResults(currentPapers, config, translationStats, true);
        showSection('results');

    } catch (error) {
        alert('加载失败: ' + error.message);
    }
}

async function deletePapersHistory(hash) {
    if (!confirm('确定要删除这条论文速递记录吗？')) return;

    try {
        const response = await fetch(`${API_BASE}/api/history/papers/${hash}`, {
            method: 'DELETE'
        });
        const data = await response.json();

        if (data.success) {
            openHistoryModal(); // 刷新列表
        } else {
            alert('删除失败: ' + data.error);
        }
    } catch (error) {
        alert('删除失败: ' + error.message);
    }
}

async function loadAgentHistory(hash) {
    try {
        const response = await fetch(`${API_BASE}/api/history/agent/${hash}`);
        const data = await response.json();

        if (!data.success || !data.record) {
            alert('加载失败: ' + (data.error || '记录不存在'));
            return;
        }

        const record = data.record;
        const result = record.result || {};
        currentAgentResult = {
            question: result.question || record.question || '',
            intent: result.intent || {},
            strategy: result.strategy || {},
            subQueries: result.subQueries || [],
            sourcePriorities: result.sourcePriorities || [],
            papers: result.papers || [],
            synthesis: result.synthesis || {},
            evaluation: result.evaluation || {},
            trajectory: result.trajectory || [],
            stats: result.stats || {},
            cached: true,
            hash: hash,
            cacheCreated: record.created || ''
        };

        closeHistoryModal();
        renderAgentResults(currentAgentResult, true);
        showSection('agentResults');
    } catch (error) {
        alert('加载失败: ' + error.message);
    }
}

async function deleteAgentHistory(hash) {
    if (!confirm('确定要删除这条研究 Agent 记录吗？')) return;

    try {
        const response = await fetch(`${API_BASE}/api/history/agent/${hash}`, {
            method: 'DELETE'
        });
        const data = await response.json();

        if (data.success) {
            openHistoryModal();
        } else {
            alert('删除失败: ' + data.error);
        }
    } catch (error) {
        alert('删除失败: ' + error.message);
    }
}

async function loadIntensiveHistory(hash) {
    try {
        const response = await fetch(`${API_BASE}/api/history/intensive/${hash}`);
        const data = await response.json();

        if (!data.success || !data.record) {
            alert('加载失败: ' + (data.error || '记录不存在'));
            return;
        }

        const record = data.record;

        // 直接显示精读结果
        currentIntensiveReadPaper = record.paper;

        document.getElementById('modalTitle').textContent = `📖 精读: ${(record.paperTitle || '').substring(0, 50)}...`;

        closeHistoryModal();
        showIntensiveReadModal();

        const loading = document.getElementById('modalLoading');
        const content = document.getElementById('modalContent');
        loading.classList.add('hidden');
        content.classList.remove('hidden');

        content.innerHTML = parseIntensiveReadMarkdown(record.result?.content || '暂无内容');
        renderMathInElement(content);
        content.dataset.rawContent = record.result?.content || '';

        // 显示缓存标签
        const processing = document.getElementById('modalProcessing');
        if (processing) processing.classList.add('hidden');

    } catch (error) {
        alert('加载失败: ' + error.message);
    }
}

async function deleteIntensiveHistory(hash) {
    if (!confirm('确定要删除这条精读记录吗？')) return;

    try {
        const response = await fetch(`${API_BASE}/api/history/intensive/${hash}`, {
            method: 'DELETE'
        });
        const data = await response.json();

        if (data.success) {
            openHistoryModal(); // 刷新列表
        } else {
            alert('删除失败: ' + data.error);
        }
    } catch (error) {
        alert('删除失败: ' + error.message);
    }
}
