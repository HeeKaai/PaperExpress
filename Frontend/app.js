/**
 * PaperExpress - 智能化论文速递
 * 前端 JavaScript
 */

// 全局状态
let currentPapers = [];
let isProcessing = false;
let categoriesLoaded = false;
let selectedCategories = new Set();

// API 基础 URL - 后端和前端在同一个服务器上，使用相对路径
const API_BASE = '';

// DOM 元素
const configSection = document.getElementById('configSection');
const progressSection = document.getElementById('progressSection');
const resultsSection = document.getElementById('resultsSection');
const startBtn = document.getElementById('startBtn');
const backBtn = document.getElementById('backBtn');
const exportBtn = document.getElementById('exportBtn');
const serverStatus = document.getElementById('serverStatus');

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

    // 返回按钮
    backBtn.addEventListener('click', () => showSection('config'));

    // 导出按钮
    exportBtn.addEventListener('click', exportToMarkdown);
}

// 加载学科分类
async function loadCategories() {
    const container = document.getElementById('categoryContainer');
    const loadBtn = document.getElementById('loadCategoriesBtn');

    container.classList.remove('hidden');
    loadBtn.disabled = true;
    loadBtn.textContent = '加载中...';

    try {
        const response = await fetch(`${API_BASE}/api/categories`);
        if (!response.ok) throw new Error('获取分类失败');

        const data = await response.json();
        const categories = data.categories;

        // 清空容器
        container.innerHTML = '';

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

        categoriesLoaded = true;
        updateSelectedCategoriesDisplay();
        updateStartButton();

    } catch (error) {
        container.innerHTML = `<div class="loading-text" style="color: var(--error-color)">加载失败: ${error.message}</div>`;
    } finally {
        loadBtn.disabled = false;
        loadBtn.textContent = '📋 重新加载分类';
    }
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
    startBtn.disabled = !hasCategories;
    if (!hasCategories) {
        startBtn.title = '请至少选择一个学科分类';
    } else {
        startBtn.title = '';
    }
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

    if (config.categories.length === 0) {
        alert('请至少选择一个学科分类');
        return;
    }

    isProcessing = true;
    currentPapers = [];

    showSection('progress');
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

        // 步骤2: 翻译论文
        updateProgress(35, '正在翻译摘要...', '开始调用 LLM 进行翻译...');

        const translatedPapers = [];
        const llmConfig = config.llm;

        for (let i = 0; i < papers.length; i++) {
            const paper = papers[i];

            try {
                const translateResponse = await fetch(`${API_BASE}/api/translate`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        paper: paper,
                        llm: llmConfig
                    })
                });

                if (!translateResponse.ok) {
                    throw new Error('翻译请求失败');
                }

                const translateData = await translateResponse.json();
                const result = translateData.result || {};

                translatedPapers.push({
                    ...paper,
                    chineseAbstract: result.chineseAbstract || '解析失败',
                    highlight: result.highlight || '解析失败'
                });

                updateProgress(
                    35 + Math.round(((i + 1) / papers.length) * 60),
                    `正在翻译: ${i + 1}/${papers.length}`,
                    `✓ [${i + 1}/${papers.length}] ${paper.title.substring(0, 50)}...`
                );

            } catch (error) {
                translatedPapers.push({
                    ...paper,
                    chineseAbstract: `翻译失败: ${error.message}`,
                    highlight: '无法生成亮点',
                    translationError: true
                });

                updateProgress(
                    35 + Math.round(((i + 1) / papers.length) * 60),
                    `正在翻译: ${i + 1}/${papers.length}`,
                    `✗ [${i + 1}/${papers.length}] ${paper.title.substring(0, 50)}... 失败: ${error.message}`
                );
            }
        }

        // 步骤3: 展示结果
        currentPapers = translatedPapers;
        renderResults(currentPapers, config);
        showSection('results');

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
        timeRange: parseInt(document.getElementById('timeRange').value),
        maxPapers: parseInt(document.getElementById('maxPapers').value),
        llm: getLLMConfig()
    };
}

// 显示区域
function showSection(section) {
    configSection.classList.add('hidden');
    progressSection.classList.add('hidden');
    resultsSection.classList.add('hidden');

    if (section === 'config') configSection.classList.remove('hidden');
    if (section === 'progress') progressSection.classList.remove('hidden');
    if (section === 'results') resultsSection.classList.remove('hidden');
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
function renderResults(papers, config) {
    const container = document.getElementById('papersContainer');
    const meta = document.getElementById('resultsMeta');

    // 元信息
    const categories = config.categories.slice(0, 5).join(', ') + (config.categories.length > 5 ? ` 等${config.categories.length}个分类` : '');
    const dateRange = `最近 ${config.timeRange} 天`;
    meta.innerHTML = `
        <strong>分类:</strong> ${categories} |
        <strong>时间范围:</strong> ${dateRange} |
        <strong>共 ${papers.length} 篇论文</strong>
    `;

    // 清空容器
    container.innerHTML = '';

    // 渲染每篇论文
    papers.forEach((paper, index) => {
        const card = document.createElement('div');
        card.className = 'paper-card';

        const authors = paper.authors.slice(0, 5).join(', ') +
            (paper.authors.length > 5 ? ` 等 ${paper.authors.length} 位作者` : '');

        const highlightHtml = paper.highlight && !paper.translationError ? `
            <div class="paper-highlight">
                <div class="paper-highlight-label">✨ 一句话亮点</div>
                <div class="paper-highlight-text">${escapeHtml(paper.highlight)}</div>
            </div>
        ` : '';

        const abstractHtml = paper.chineseAbstract ? `
            <div class="paper-abstract">
                <div class="paper-abstract-label">📝 中文摘要</div>
                <div>${escapeHtml(paper.chineseAbstract)}</div>
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
                <a href="${paper.pdfLink || paper.link}" target="_blank" rel="noopener" class="paper-link">
                    📄 查看原文
                </a>
            </div>
        `;

        container.appendChild(card);
    });
}

// 导出为 Markdown
function exportToMarkdown() {
    if (currentPapers.length === 0) return;

    const date = new Date().toLocaleDateString('zh-CN');
    let markdown = `# 📄 PaperExpress 论文速递\n\n`;
    markdown += `**生成日期:** ${date}  \n`;
    markdown += `**论文数量:** ${currentPapers.length} 篇\n\n`;
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
