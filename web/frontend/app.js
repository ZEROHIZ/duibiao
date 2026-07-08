/**
 * 信息源监控看板 — 前端逻辑控制 (app.js)
 * 核心职责：处理 Tab 页面路由、图表渲染、API 请求（包含博主物理蒸馏文件检测与获取）及 GSAP 视觉动效。
 */

// 1. 全局配置与状态
const API_BASE = ""; // 因为是同源托管，使用相对路径即可
let currentTab = "dashboard";
let activeBloggerName = null;
let categoryChartInstance = null; // 用于存储 Chart.js 实例防止重绘冲突
let bloggerLayoutMode = "table"; // 默认表格管理视图
let bloggerSubTab = "list"; // 默认博主监控列表

// 2. 初始化加载
document.addEventListener("DOMContentLoaded", () => {
    initTheme();
    setupEventListeners();
    switchTab("dashboard"); // 默认展示仪表盘
});

// 3. 主题系统 (Dark / Light Mode Toggle)
function initTheme() {
    const savedTheme = localStorage.getItem("theme");
    const systemPrefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    
    if (savedTheme === "dark" || (!savedTheme && systemPrefersDark)) {
        document.documentElement.setAttribute("data-theme", "dark");
        document.getElementById("theme-toggle").innerText = "LIGHT MODE";
    } else {
        document.documentElement.setAttribute("data-theme", "light");
        document.getElementById("theme-toggle").innerText = "DARK MODE";
    }
}

function toggleTheme() {
    const currentTheme = document.documentElement.getAttribute("data-theme");
    const toggleBtn = document.getElementById("theme-toggle");
    
    if (currentTheme === "dark") {
        document.documentElement.setAttribute("data-theme", "light");
        localStorage.setItem("theme", "light");
        toggleBtn.innerText = "DARK MODE";
    } else {
        document.documentElement.setAttribute("data-theme", "dark");
        localStorage.setItem("theme", "dark");
        toggleBtn.innerText = "LIGHT MODE";
    }
    
    // 如果博主图表已渲染，在主题切换后重新渲染以配合文字颜色
    if (activeBloggerName) {
        refreshCategoryChartTheme();
    }
}

// 4. 事件监听器配置
function setupEventListeners() {
    // 顶栏 Tab 导航点击事件
    document.querySelectorAll(".nav-tab").forEach(tab => {
        tab.addEventListener("click", (e) => {
            const tabId = e.target.getAttribute("data-tab");
            switchTab(tabId);
        });
    });

    // 仪表盘指标卡快捷点击跳转
    document.querySelectorAll(".stat-card").forEach(card => {
        card.addEventListener("click", (e) => {
            const targetTab = e.currentTarget.getAttribute("data-tab-link");
            if (targetTab) switchTab(targetTab);
        });
    });

    // 主题切换按钮
    document.getElementById("theme-toggle").addEventListener("click", toggleTheme);

    // 思维模型快速添加表单提交
    const quickForm = document.getElementById("quick-knowledge-form");
    if (quickForm) {
        quickForm.addEventListener("submit", handleQuickKnowledgeSubmit);
    }

    // 思维模型搜索监听 (防抖处理)
    let searchTimeout;
    const searchInput = document.getElementById("k-search");
    if (searchInput) {
        searchInput.addEventListener("input", (e) => {
            clearTimeout(searchTimeout);
            searchTimeout = setTimeout(() => {
                loadKnowledgeBaseData(null, e.target.value);
            }, 300);
        });
    }

    // 返回博主列表按钮
    document.getElementById("btn-back-bloggers").addEventListener("click", () => {
        showBloggerSubview("list");
    });

    // AI 蒸馏报告跳转按钮（右上角）
    const btnOpenReport = document.getElementById("btn-open-ai-report");
    if (btnOpenReport) {
        btnOpenReport.addEventListener("click", () => {
            const reportUrl = btnOpenReport.getAttribute("data-report-url");
            if (reportUrl) {
                window.open(reportUrl, "_blank");
            } else {
                alert(`博主“${activeBloggerName}”的 AI 蒸馏报告尚未生成（请先运行后台蒸馏脚本生成该文件）。`);
            }
        });
    }

    // 同步更新数据按钮事件 (轻量 Toast 排队模式，支持数量自定义)
    const btnSyncCrawler = document.getElementById("btn-sync-crawler");
    if (btnSyncCrawler) {
        btnSyncCrawler.addEventListener("click", () => {
            if (!activeBloggerName) {
                showToast("未选定当前激活的博主！", "error");
                return;
            }
            
            const maxVideosInput = document.getElementById("detail-max-videos");
            const maxVideos = maxVideosInput ? parseInt(maxVideosInput.value) : 5;
            
            showToast(`已开始将博主“${activeBloggerName}”的同步任务(抓取 ${maxVideos} 条)提交至后台队列...`, "info");
            
            // 请求后端开始同步，传递 max_videos 覆盖参数
            fetch(`${API_BASE}/api/crawl/run?blogger=${encodeURIComponent(activeBloggerName)}&max_videos=${maxVideos}`, { method: "POST" })
                .then(res => res.json())
                .then(json => {
                    if (json.status === "success" && json.task_id) {
                        showToast(`已加入同步队列！正在排队执行。您可以在‘任务日志’页查看进度。`, "success");
                        if (currentTab === "logs") {
                            loadSettingsPageTasks();
                        }
                    } else {
                        throw new Error(json.message || "后端任务创建失败");
                    }
                })
                .catch(err => {
                    showToast(`启动同步任务失败: ${err.message}`, "error");
                });
        });
    }

    // 系统设置页面专属事件绑定
    const settingsForm = document.getElementById("system-settings-form");
    if (settingsForm) {
        settingsForm.addEventListener("submit", handleSystemSettingsSubmit);
    }
    
    const btnSyncAll = document.getElementById("btn-sync-all");
    if (btnSyncAll) {
        btnSyncAll.addEventListener("click", handleSyncAllClick);
    }
    
    const btnClearHistory = document.getElementById("btn-clear-history");
    if (btnClearHistory) {
        btnClearHistory.addEventListener("click", handleClearHistoryClick);
    }
    
    const btnTranscribeNow = document.getElementById("btn-transcribe-now");
    if (btnTranscribeNow) {
        btnTranscribeNow.addEventListener("click", handleTranscribeNowClick);
    }
    
    // 绑定任务日志类型的分页选项卡点击切换事件
    const btnTabSync = document.getElementById("task-tab-sync");
    const btnTabTranscribe = document.getElementById("task-tab-transcribe");
    if (btnTabSync && btnTabTranscribe) {
        btnTabSync.addEventListener("click", () => {
            currentTaskTab = "sync";
            btnTabSync.style.color = "var(--accent-primary)";
            btnTabSync.style.borderBottom = "2px solid var(--accent-primary)";
            btnTabTranscribe.style.color = "var(--ink-secondary)";
            btnTabTranscribe.style.borderBottom = "none";
            loadSettingsPageTasks();
        });
        btnTabTranscribe.addEventListener("click", () => {
            currentTaskTab = "transcribe";
            btnTabTranscribe.style.color = "var(--accent-primary)";
            btnTabTranscribe.style.borderBottom = "2px solid var(--accent-primary)";
            btnTabSync.style.color = "var(--ink-secondary)";
            btnTabSync.style.borderBottom = "none";
            loadSettingsPageTasks();
        });
    }




    // 博主详情内页 Tab 点击事件
    document.querySelectorAll(".inner-tab").forEach(tab => {
        tab.addEventListener("click", (e) => {
            const detailTabId = e.target.getAttribute("data-detail-tab");
            if (!detailTabId) return;
            switchBloggerDetailTab(detailTabId);
        });
    });

    // 表格/网格切换按钮事件
    const btnTable = document.getElementById("toggle-view-table");
    const btnGrid = document.getElementById("toggle-view-grid");
    if (btnTable && btnGrid) {
        btnTable.addEventListener("click", () => {
            btnGrid.classList.remove("active");
            btnTable.classList.add("active");
            switchBloggerLayout("table");
        });
        btnGrid.addEventListener("click", () => {
            btnTable.classList.remove("active");
            btnGrid.classList.add("active");
            switchBloggerLayout("grid");
        });
    }

    // 新增博主折叠按钮事件
    const btnToggleAdd = document.getElementById("btn-toggle-add-blogger");
    if (btnToggleAdd) {
        btnToggleAdd.addEventListener("click", toggleAddBloggerForm);
    }

    // 新增博主表单提交事件
    const addBloggerForm = document.getElementById("add-blogger-form");
    if (addBloggerForm) {
        addBloggerForm.addEventListener("submit", handleAddBloggerSubmit);
    }

    // 对标子页签事件
    const subtabList = document.getElementById("subtab-bloggers-list");
    const subtabTimeline = document.getElementById("subtab-notes-timeline");
    if (subtabList && subtabTimeline) {
        subtabList.addEventListener("click", () => switchBloggerSubTab("list"));
        subtabTimeline.addEventListener("click", () => switchBloggerSubTab("timeline"));
    }

    // 复制创作指南/灵魂底稿原始 Markdown
    function bindCopyBtn(btnId, contentId) {
        const btn = document.getElementById(btnId);
        if (!btn) return;
        btn.addEventListener("click", () => {
            const rawMd = document.getElementById(contentId)?.dataset.rawMd;
            if (!rawMd) {
                btn.textContent = "内容尚未加载";
                setTimeout(() => { btn.textContent = "复制"; }, 1500);
                return;
            }
            navigator.clipboard.writeText(rawMd).then(() => {
                btn.textContent = "✓ 已复制";
                setTimeout(() => { btn.textContent = "复制"; }, 1800);
            }).catch(() => {
                btn.textContent = "复制失败";
                setTimeout(() => { btn.textContent = "复制"; }, 1800);
            });
        });
    }
    bindCopyBtn("btn-copy-skill", "ai-skill-content");
    bindCopyBtn("btn-copy-soul",  "ai-soul-content");
}

// Toast 弹窗通知辅助函数 (全局作用域)
function showToast(message, type = "info") {
    const container = document.getElementById("toast-container");
    if (!container) return;
    
    const toast = document.createElement("div");
    toast.className = "toast";
    
    // 状态边框色适配
    if (type === "success") {
        toast.style.borderColor = "#4a8a5f";
    } else if (type === "error") {
        toast.style.borderColor = "#c94f3b";
    }
    
    toast.innerHTML = `
        <span>${message}</span>
        <button class="toast-close">✕</button>
    `;
    
    container.appendChild(toast);
    
    // 绑定关闭按钮
    toast.querySelector(".toast-close").addEventListener("click", () => {
        gsap.to(toast, {
            opacity: 0,
            y: -10,
            duration: 0.2,
            onComplete: () => toast.remove()
        });
    });
    
    // GSAP 飞入动画
    gsap.to(toast, {
        opacity: 1,
        y: 0,
        duration: 0.35,
        ease: "power2.out"
    });
    
    // 4秒后自动移除
    setTimeout(() => {
        if (toast.parentNode) {
            gsap.to(toast, {
                opacity: 0,
                y: -10,
                duration: 0.2,
                onComplete: () => toast.remove()
            });
        }
    }, 4000);
}

// 5. 核心：单页面应用 (SPA) Tab 切换控制与 GSAP 动画
function switchTab(tabId) {
    if (currentTab === tabId && document.getElementById(`page-${tabId}`).classList.contains("active")) {
        return;
    }

    const prevSection = document.querySelector(".page-section.active");
    const nextSection = document.getElementById(`page-${tabId}`);
    
    if (!nextSection) return;

    currentTab = tabId;

    // 切换导航按钮高亮状态
    document.querySelectorAll(".nav-tab").forEach(btn => btn.classList.remove("active"));
    const activeNav = document.querySelector(`.nav-tab[data-tab="${tabId}"]`);
    if (activeNav) activeNav.classList.add("active");

    // GSAP 动画过渡 (如果系统启用减少动画，则降级为无缝切换)
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches || !prevSection) {
        if (prevSection) prevSection.classList.remove("active");
        nextSection.classList.add("active");
        loadTabData(tabId);
    } else {
        gsap.to(prevSection, {
            opacity: 0,
            y: -10,
            duration: 0.2,
            ease: "power2.in",
            onComplete: () => {
                prevSection.classList.remove("active");
                nextSection.classList.add("active");
                
                // 加载目标页数据
                loadTabData(tabId);
                
                gsap.fromTo(nextSection, 
                    { opacity: 0, y: 15 },
                    { opacity: 1, y: 0, duration: 0.45, ease: "power3.out" }
                );
            }
        });
    }
}

// 6. 数据中心加载路由
function loadTabData(tabId) {
    // 每次切换时都更新顶部的整体指标数据
    fetchDashboardStats();

    switch (tabId) {
        case "dashboard":
            break;
        case "knowledge":
            loadKnowledgeBaseData();
            break;
        case "bloggers":
            if (!activeBloggerName) {
                showBloggerSubview("list");
            }
            break;
        case "news":
            loadIndustryNewsData();
            break;
        case "trending":
            loadTrendingTopicsData();
            break;
        case "settings":
            loadSettingsPageData();
            break;
        case "logs":
            loadLogsPageData();
            break;
    }
}

// 7. 仪表盘数据接口与表单提交
async function fetchDashboardStats() {
    try {
        const res = await fetch(`${API_BASE}/api/dashboard`);
        const json = await res.json();
        if (json.status === "success") {
            const d = json.data;
            document.getElementById("stat-k-count").innerText = d.knowledge_count;
            document.getElementById("stat-b-count").innerText = d.bloggers_count;
            document.getElementById("stat-n-count").innerText = d.news_count;
            document.getElementById("stat-t-count").innerText = d.trending_count;
        }
    } catch (e) {
        console.error("Failed to fetch dashboard stats", e);
    }
}

async function handleQuickKnowledgeSubmit(e) {
    e.preventDefault();
    const topic = document.getElementById("form-topic").value.trim();
    const niche = document.getElementById("form-niche").value;
    const insight = document.getElementById("form-insight").value;
    const pitfall = document.getElementById("form-pitfall").value;
    const analogy = document.getElementById("form-analogy").value;

    const payload = { topic, niche, insight, pitfall, analogy };

    try {
        const res = await fetch(`${API_BASE}/api/knowledge`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });

        if (res.ok) {
            alert(`思维模型《${topic}》已成功存盘！`);
            document.getElementById("quick-knowledge-form").reset();
            fetchDashboardStats();
        } else {
            const err = await res.json();
            alert(`保存失败: ${err.detail || "未知错误"}`);
        }
    } catch (err) {
        alert("网络连接失败，请检查后端运行状态。");
    }
}

// 8. 理论与思维模型卡片逻辑 (Feed 1)
async function loadKnowledgeBaseData(niche = null, query = null) {
    const container = document.getElementById("knowledge-list-container");
    container.innerHTML = `<div class="lead-text">数据加载中...</div>`;

    let url = `${API_BASE}/api/knowledge`;
    const params = [];
    if (niche && niche !== "all") params.push(`niche=${encodeURIComponent(niche)}`);
    if (query) params.push(`q=${encodeURIComponent(query)}`);
    if (params.length > 0) url += `?${params.join("&")}`;

    try {
        const res = await fetch(url);
        const data = await res.json();
        
        if (!niche && !query) {
            renderNicheFilters(data);
        }

        if (data.length === 0) {
            container.innerHTML = `<div class="lead-text" style="font-style: italic;">没有匹配的思维模型。</div>`;
            return;
        }

        container.innerHTML = "";
        data.forEach(item => {
            const card = document.createElement("div");
            card.className = "k-card";
            card.innerHTML = `
                <div class="k-card-header">
                    <h3 class="k-card-title">${item.topic}</h3>
                    <span class="k-card-niche">${item.niche}</span>
                </div>
                <div class="k-text" style="font-size: 1.05rem; font-weight: 500; margin-bottom: 1rem;">
                    ${item.insight}
                </div>
                <div class="k-card-row">
                    <div>
                        <span class="k-label">大众常犯的误区</span>
                        <div class="k-text">${item.pitfall}</div>
                    </div>
                    <div>
                        <span class="k-label">生活化类比</span>
                        <div class="k-text">${item.analogy}</div>
                    </div>
                </div>
            `;
            container.appendChild(card);
        });

        if (!window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
            gsap.from(".k-card", {
                opacity: 0,
                y: 15,
                duration: 0.5,
                stagger: 0.08,
                ease: "power2.out"
            });
        }
    } catch (e) {
        container.innerHTML = `<div class="lead-text" style="color: var(--accent-primary);">无法加载模型库数据，请确保后端服务正常。</div>`;
    }
}

function renderNicheFilters(data) {
    const filterContainer = document.getElementById("k-niche-filters");
    filterContainer.innerHTML = `<button class="filter-tag active" data-niche="all">全部赛道</button>`;
    
    const niches = new Set();
    data.forEach(item => {
        if (item.niche) {
            item.niche.split(/[/,，\s]+/).forEach(n => {
                const cleanN = n.trim();
                if (cleanN) niches.add(cleanN);
            });
        }
    });

    niches.forEach(niche => {
        const btn = document.createElement("button");
        btn.className = "filter-tag";
        btn.setAttribute("data-niche", niche);
        btn.innerText = niche;
        btn.addEventListener("click", (e) => {
            document.querySelectorAll(".filter-tag").forEach(b => b.classList.remove("active"));
            e.target.classList.add("active");
            loadKnowledgeBaseData(niche);
        });
        filterContainer.appendChild(btn);
    });

    filterContainer.querySelector('[data-niche="all"]').addEventListener("click", (e) => {
        document.querySelectorAll(".filter-tag").forEach(b => b.classList.remove("active"));
        e.target.classList.add("active");
        loadKnowledgeBaseData("all");
    });
}

// 9. 对标博主管理与子页签切换
function switchBloggerSubTab(tab) {
    bloggerSubTab = tab;
    const subtabList = document.getElementById("subtab-bloggers-list");
    const subtabTimeline = document.getElementById("subtab-notes-timeline");
    const listView = document.getElementById("blogger-list-view");
    const timelineView = document.getElementById("blogger-timeline-view");

    if (tab === "list") {
        subtabTimeline.classList.remove("active");
        subtabList.classList.add("active");
        timelineView.classList.remove("active-subview");
        listView.classList.add("active-subview");
        loadBloggersList();
    } else {
        subtabList.classList.remove("active");
        subtabTimeline.classList.add("active");
        listView.classList.remove("active-subview");
        timelineView.classList.add("active-subview");
        loadAllWorksTimeline();
    }
}

function switchBloggerLayout(mode) {
    bloggerLayoutMode = mode;
    const tableContainer = document.getElementById("blogger-table-container");
    const gridContainer = document.getElementById("blogger-grid-container");
    
    if (mode === "table") {
        gridContainer.classList.remove("active-layout");
        tableContainer.classList.add("active-layout");
    } else {
        tableContainer.classList.remove("active-layout");
        gridContainer.classList.add("active-layout");
    }
}

function showBloggerSubview(view) {
    const headerSec = document.getElementById("blogger-header-section");
    if (view === "list") {
        activeBloggerName = null;
        document.getElementById("blogger-detail-view").classList.remove("active-subview");
        if (headerSec) headerSec.style.display = "block";
        switchBloggerSubTab(bloggerSubTab);
    } else {
        document.getElementById("blogger-list-view").classList.remove("active-subview");
        document.getElementById("blogger-timeline-view").classList.remove("active-subview");
        document.getElementById("blogger-detail-view").classList.add("active-subview");
        if (headerSec) headerSec.style.display = "none";
    }
}

function toggleAddBloggerForm() {
    const container = document.getElementById("add-blogger-form-container");
    if (!container) return;

    if (container.style.display === "none") {
        container.style.display = "block";
        gsap.fromTo(container, { opacity: 0, y: -10 }, { opacity: 1, y: 0, duration: 0.3, ease: "power2.out" });
    } else {
        gsap.to(container, {
            opacity: 0,
            y: -10,
            duration: 0.2,
            ease: "power2.in",
            onComplete: () => {
                container.style.display = "none";
            }
        });
    }
}

async function handleAddBloggerSubmit(e) {
    e.preventDefault();
    const nameInput = document.getElementById("add-form-name");
    const urlInput = document.getElementById("add-form-url");

    if (!nameInput) return;

    const payload = {
        name: nameInput.value.trim(),
        home_url: urlInput ? urlInput.value.trim() : ""
    };

    try {
        const res = await fetch(`${API_BASE}/api/bloggers`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });

        if (res.ok) {
            alert(`博主“${payload.name}”成功录入数据库监控队列！`);
            document.getElementById("add-blogger-form").reset();
            document.getElementById("add-blogger-form-container").style.display = "none";
            fetchDashboardStats();
            loadBloggersList();
        } else {
            const err = await res.json();
            alert(`录入失败: ${err.detail || "错误"}`);
        }
    } catch (e) {
        alert("接口调用失败，请确认后端运行状态。");
    }
}

async function loadBloggersList() {
    const tableBody = document.querySelector("#table-bloggers-management tbody");
    const gridContainer = document.getElementById("blogger-grid-container");
    
    tableBody.innerHTML = `<tr><td colspan="7" class="lead-text">加载对标账号中...</td></tr>`;
    gridContainer.innerHTML = `<div class="lead-text">加载对标账号中...</div>`;

    try {
        // 使用时间戳参数进行防浏览器缓存处理，保证删除/修改后立即可见
        const res = await fetch(`${API_BASE}/api/bloggers?t=${Date.now()}`);
        const data = await res.json();

        if (data.length === 0) {
            const emptyHtml = `<tr><td colspan="7" style="font-style: italic;" class="lead-text">暂无对标账号，点击上方“录入监控博主”进行添加。</td></tr>`;
            tableBody.innerHTML = emptyHtml;
            gridContainer.innerHTML = `<div class="lead-text" style="font-style: italic;">暂无对标账号。</div>`;
            return;
        }

        // 1. 渲染表格管理视图
        tableBody.innerHTML = "";
        data.forEach(b => {
            const tr = document.createElement("tr");
            tr.style.cursor = "pointer";
            const urlVal = b.home_url || "";
            
            // 处理最新视频数据
            const latestTitle = b.latest_note_title ? b.latest_note_title.substring(0, 20) + "..." : "待更新/无数据";
            const latestTime = b.latest_note_time ? b.latest_note_time.substring(5, 16) : "—";
            
            // 是否有深度蒸馏报告判断
            const hasDistilled = b.total_notes > 0;
            // 允许所有博主都可以点击进入详情页，对于未同步博主也提供详情按钮以方便同步
            const distillActionHtml = `<button class="btn-text" style="color: var(--accent-primary)" onclick="loadBloggerDetail('${b.name}')">蒸馏拆解</button>`;

            // 仅保留删除操作按钮，改名改为双击文字
            const deleteHtml = `<button class="btn-text" style="color: var(--accent-primary); margin-left: 0.75rem;" onclick="deleteBloggerConfirm(${b.id}, '${b.name}')">删除</button>`;
            const actionsHtml = `<div style="display: flex; align-items: center; justify-content: flex-start;">${distillActionHtml}${deleteHtml}</div>`;

            tr.innerHTML = `
                <td>
                    <span class="editable-field" data-id="${b.id}" data-field="name" title="双击可直接修改博主名称" style="cursor: pointer; border-bottom: 1px dashed var(--ink-secondary); font-size: 1.05rem; font-family: var(--font-serif); font-weight: 600; display: inline-block; padding-bottom: 2px;">${b.name}</span>
                </td>
                <td>
                    <span class="editable-field" data-id="${b.id}" data-field="home_url" title="双击可直接修改监控主页链接" style="cursor: pointer; border-bottom: 1px dashed var(--ink-secondary); font-family: var(--font-mono); font-size: 0.85rem; max-width: 320px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; display: inline-block; padding-bottom: 2px; vertical-align: middle;">${urlVal || "双击配置个人主页链接..."}</span>
                </td>
                <td style="font-family: var(--font-serif); color: var(--accent-primary); font-weight: 500;">
                    ${hasDistilled ? b.avg_likes.toLocaleString() : '待同步/0'}
                </td>
                <td>
                    <span style="font-size: 0.85rem; color: var(--ink-secondary)">
                        ${hasDistilled ? `${b.avg_collects.toLocaleString()} / ${b.avg_comments.toLocaleString()}` : '待同步/0'}
                    </span>
                </td>
                <td style="font-size: 0.9rem; font-style: italic; color: var(--ink-secondary); max-width: 200px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
                    ${latestTitle}
                </td>
                <td style="font-size: 0.85rem; color: var(--ink-tertiary);">${latestTime}</td>
                <td>
                    ${actionsHtml}
                </td>
            `;

            // 单击整行（避开编辑状态、按钮）即可直接进入深度蒸馏页面
            tr.addEventListener("click", (e) => {
                if (e.target.closest("button") || e.target.closest("input") || e.target.closest(".editable-field")) {
                    return;
                }
                loadBloggerDetail(b.name);
            });

            tableBody.appendChild(tr);
        });

        // 绑定双击编辑事件
        tableBody.querySelectorAll(".editable-field").forEach(el => {
            el.addEventListener("dblclick", (e) => {
                startInlineEdit(e.currentTarget);
            });
        });

        // 2. 渲染网格卡片视图
        gridContainer.innerHTML = "";
        data.forEach(b => {
            const card = document.createElement("div");
            card.className = "blogger-item-card";
            const hasDistilled = b.total_notes > 0;
            card.innerHTML = `
                <h3 class="blogger-item-name">${b.name}</h3>
                <div class="blogger-item-stat-row">
                    <span>总采集笔记数</span>
                    <span class="blogger-item-stat-val">${hasDistilled ? `${b.total_notes} 条` : '0'}</span>
                </div>
                <div class="blogger-item-stat-row">
                    <span>均赞表现</span>
                    <span class="blogger-item-stat-val" style="color: var(--accent-primary); font-family: var(--font-serif); font-size: 1.1rem;">
                        ${hasDistilled ? b.avg_likes.toLocaleString() : '0'}
                    </span>
                </div>
            `;
            card.addEventListener("click", () => {
                loadBloggerDetail(b.name);
            });
            if (!hasDistilled) {
                card.style.opacity = "0.8";
                card.style.cursor = "pointer";
            }
            gridContainer.appendChild(card);
        });

        switchBloggerLayout(bloggerLayoutMode);

        if (!window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
            if (bloggerLayoutMode === "table") {
                gsap.from("#table-bloggers-management tbody tr", {
                    opacity: 0,
                    x: -10,
                    duration: 0.4,
                    stagger: 0.05,
                    ease: "power2.out"
                });
            } else {
                gsap.from(".blogger-item-card", {
                    opacity: 0,
                    y: 15,
                    duration: 0.4,
                    stagger: 0.08,
                    ease: "power2.out"
                });
            }
        }
    } catch (e) {
        tableBody.innerHTML = `<tr><td colspan="7" style="color: var(--accent-primary)">数据加载失败</td></tr>`;
        gridContainer.innerHTML = `<div class="lead-text" style="color: var(--accent-primary)">无法加载博主列表。</div>`;
    }
}

// 双击编辑内联元素实现函数
function startInlineEdit(el) {
    if (el.classList.contains("editing")) return;
    el.classList.add("editing");
    
    const bloggerId = el.getAttribute("data-id");
    const field = el.getAttribute("data-field");
    const originalValue = el.innerText === "双击配置个人主页链接..." ? "" : el.innerText;
    
    const input = document.createElement("input");
    input.type = "text";
    input.value = originalValue;
    input.style.width = "100%";
    input.style.fontFamily = field === "home_url" ? "var(--font-mono)" : "var(--font-sans)";
    input.style.fontSize = el.style.fontSize;
    input.style.fontWeight = el.style.fontWeight;
    input.style.border = "1px solid var(--accent-primary)";
    input.style.background = "var(--bg-secondary)";
    input.style.color = "var(--ink-primary)";
    input.style.padding = "0.2rem 0.4rem";
    input.style.boxSizing = "border-box";
    
    el.innerHTML = "";
    el.appendChild(input);
    input.focus();
    input.select();
    
    let finished = false;
    
    const finishEdit = async (save) => {
        if (finished) return;
        finished = true;
        
        const newValue = input.value.trim();
        if (save && newValue !== originalValue) {
            el.innerText = "保存中...";
            try {
                const url = field === "home_url" 
                    ? `${API_BASE}/api/bloggers/${bloggerId}/home_url`
                    : `${API_BASE}/api/bloggers/${bloggerId}/name`;
                
                const payload = field === "home_url" 
                    ? { home_url: newValue }
                    : { name: newValue };
                    
                const res = await fetch(url, {
                    method: "PUT",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(payload)
                });
                
                if (res.ok) {
                    showToast("保存成功", "success");
                    loadBloggersList();
                    if (field === "name") {
                        fetchDashboardStats();
                    }
                } else {
                    const err = await res.json();
                    showToast(`修改失败: ${err.detail || "冲突或错误"}`, "error");
                    el.innerText = originalValue || (field === "home_url" ? "双击配置个人主页链接..." : "");
                    el.classList.remove("editing");
                }
            } catch (err) {
                showToast("网络请求失败", "error");
                el.innerText = originalValue || (field === "home_url" ? "双击配置个人主页链接..." : "");
                el.classList.remove("editing");
            }
        } else {
            el.innerText = originalValue || (field === "home_url" ? "双击配置个人主页链接..." : "");
            el.classList.remove("editing");
        }
    };
    
    input.addEventListener("blur", () => {
        finishEdit(true);
    });
    
    input.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
            finishEdit(true);
        } else if (e.key === "Escape") {
            finishEdit(false);
        }
    });
}

// 渲染全局最新作品时间流总览
async function loadAllWorksTimeline() {
    const tbody = document.querySelector("#table-timeline-notes tbody");
    tbody.innerHTML = `<tr><td colspan="8" class="lead-text">查询全部作品时间流中...</td></tr>`;

    try {
        const res = await fetch(`${API_BASE}/api/notes/all?limit=50`);
        const data = await res.json();

        if (data.length === 0) {
            tbody.innerHTML = `<tr><td colspan="8" style="font-style: italic;" class="lead-text">暂无作品数据。请先录入账号并完成爬虫采集入库。</td></tr>`;
            return;
        }

        tbody.innerHTML = "";
        data.forEach(note => {
            const tr = document.createElement("tr");
            tr.setAttribute("id", `timeline-row-${note.id}`);
            const pubTime = note.published_at ? note.published_at.substring(5, 16) : "—";
            
            tr.innerHTML = `
                <td><span style="font-size: 0.85rem; color: var(--ink-secondary); font-weight: 500;">${pubTime}</span></td>
                <td><strong style="color: var(--ink-primary);">${note.blogger_name}</strong></td>
                <td><div style="font-weight: 500; font-family: var(--font-serif);">${note.title}</div></td>
                <td><span style="font-size: 0.8rem; background-color: var(--bg-secondary); padding: 0.15rem 0.4rem;">${note.type === 'video' ? '视频' : '图文'}</span></td>
                <td style="color: var(--accent-primary); font-family: var(--font-serif);">${note.likes.toLocaleString()}</td>
                <td>${note.collects.toLocaleString()}</td>
                <td>${note.comments.toLocaleString()}</td>
                <td>
                    <button class="btn-text" style="color: var(--accent-primary); font-weight: 600;" onclick="toggleCommentsDrawer('${note.id}')">查看热评</button>
                </td>
            `;
            tbody.appendChild(tr);

            // 折叠评论行
            const commentTr = document.createElement("tr");
            commentTr.className = "row-comments-drawer";
            commentTr.setAttribute("id", `comments-drawer-${note.id}`);
            
            // 格式化正文/转录状态
            let descHtml = "";
            const isUrl = note.desc && (note.desc.startsWith("http://") || note.desc.startsWith("https://"));
            const isFailed = note.desc && note.desc.startsWith("[转录失败]");
            if (isUrl) {
                descHtml = `<span style="color: var(--ink-secondary); font-size: 0.82rem; font-style: italic;">⏳ 视频已导入，后台语音转录队列正在排队处理中... (直链: <a href="${note.desc}" target="_blank" style="color: var(--accent-primary); text-decoration: underline;">在新窗口播放</a>)</span>`;
            } else if (isFailed) {
                const cleanUrl = note.desc.includes("http") ? note.desc.substring(note.desc.indexOf("http")) : "#";
                descHtml = `<span style="color: var(--accent-primary); font-size: 0.82rem; font-style: italic;">❌ 语音转译失败 (Whisper 服务繁忙)。原视频链接: <a href="${cleanUrl}" target="_blank" style="color: var(--accent-primary); text-decoration: underline;">点击去原视频播放</a></span>`;
            } else {
                descHtml = note.desc || "无描述文本";
            }

            let commentsHtml = "";
            const commentsList = note.comments_list || [];
            if (commentsList.length > 0) {
                commentsList.forEach(c => {
                    const isAuthorBadge = c.is_author ? ` <span class="k-card-niche" style="font-size:0.65rem; padding: 0.05rem 0.25rem;">作者回复</span>` : "";
                    commentsHtml += `
                        <div class="drawer-comment-item">
                            <span class="drawer-comment-user">${c.user}${isAuthorBadge}：</span>
                            <div class="drawer-comment-content">${c.content}</div>
                        </div>
                    `;
                });
            } else {
                commentsHtml = `<div class="lead-text" style="font-style: italic;">暂无采集到的热门评论，待后续脚本自动同步。</div>`;
            }

            commentTr.innerHTML = `
                <td colspan="8">
                    <div class="comments-drawer-inner" id="drawer-inner-${note.id}">
                        <div style="margin-bottom: 1rem; padding-bottom: 1rem; border-bottom: 1px dashed var(--border-color);">
                            <h4 class="comments-drawer-title" style="margin-bottom: 0.4rem;">作品文案 / 视频语音转录</h4>
                            <p style="font-size: 0.85rem; line-height: 1.6; color: var(--ink-secondary); white-space: pre-wrap; margin-bottom: 0;">
                                ${descHtml}
                            </p>
                        </div>
                        <h4 class="comments-drawer-title">脱敏热门评论与作者互动监控</h4>
                        <div class="drawer-comments-box">
                            ${commentsHtml}
                        </div>
                    </div>
                </td>
            `;
            tbody.appendChild(commentTr);
        });

        // 动画
        if (!window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
            gsap.from("#table-timeline-notes tbody tr", {
                opacity: 0,
                x: -10,
                duration: 0.4,
                stagger: 0.04,
                ease: "power2.out"
            });
        }
    } catch (e) {
        tbody.innerHTML = `<tr><td colspan="8" style="color: var(--accent-primary)">读取作品时间流失败</td></tr>`;
    }
}

// 修改主页链接 API 对接
// 删除博主 API 对接（使用自定义模态框取代系统 confirm 弹窗）
function deleteBloggerConfirm(bloggerId, name) {
    const modal = document.getElementById("delete-modal-overlay");
    const modalBody = document.getElementById("delete-modal-body");
    const confirmBtn = document.getElementById("btn-delete-confirm");
    const cancelBtn = document.getElementById("btn-delete-cancel");
    
    if (!modal || !modalBody || !confirmBtn || !cancelBtn) {
        // Fallback to system confirm
        if (confirm(`警告：您确定要删除对标博主“${name}”吗？此操作会同时级联删除该博主关联的全部笔记及分析数据，且不可恢复！`)) {
            executeDelete(bloggerId);
        }
        return;
    }
    
    modalBody.innerHTML = `警告：您确定要删除对标博主“<strong>${name}</strong>”吗？<br/><br/>此操作会同时级联删除该博主在 SQLite 中的<b>全部作品/笔记数据、作者评论以及 AI 蒸馏分析分析结果</b>，且完全不可恢复！`;
    modal.style.display = "flex";
    
    // 克隆按钮清除之前的事件绑定以防累积
    const newConfirmBtn = confirmBtn.cloneNode(true);
    confirmBtn.parentNode.replaceChild(newConfirmBtn, confirmBtn);
    
    const newCancelBtn = cancelBtn.cloneNode(true);
    cancelBtn.parentNode.replaceChild(newCancelBtn, cancelBtn);
    
    newCancelBtn.addEventListener("click", () => {
        modal.style.display = "none";
    });
    
    newConfirmBtn.addEventListener("click", async () => {
        modal.style.display = "none";
        await executeDelete(bloggerId);
    });
}

async function executeDelete(bloggerId) {
    try {
        const res = await fetch(`${API_BASE}/api/bloggers/${bloggerId}`, {
            method: "DELETE"
        });

        if (res.ok) {
            showToast("博主已成功删除！", "success");
            fetchDashboardStats();
            loadBloggersList();
        } else {
            const err = await res.json();
            showToast(`删除失败: ${err.detail || "未知错误"}`, "error");
        }
    } catch (e) {
        showToast("网络连接失败，请确认后端服务状态。", "error");
    }
}

// 加载指定博主的深度蒸馏细节
async function loadBloggerDetail(bloggerName) {
    activeBloggerName = bloggerName;
    showBloggerSubview("detail");
    
    // 初始化更新数量为全局设置的值
    const globalMaxVideosInput = document.getElementById("setting-max-videos");
    const detailMaxVideosInput = document.getElementById("detail-max-videos");
    if (globalMaxVideosInput && detailMaxVideosInput) {
        detailMaxVideosInput.value = globalMaxVideosInput.value || 5;
    }
    
    switchBloggerDetailTab("overview");

    try {
        const res = await fetch(`${API_BASE}/api/bloggers/${encodeURIComponent(bloggerName)}/distill`);
        const data = await res.json();
        
        renderBloggerProfileHeader(data.blogger);
        renderBloggerOverviewTab(data);
        renderBloggerWritingTab(data.distilled);
        renderBloggerCognitiveTab(data.distilled);
        
        loadBloggerNotesList(bloggerName);
        
        // 异步读取物理蒸馏报告及创作指南
        loadBloggerPhysicalFiles(bloggerName);
    } catch (e) {
        console.error("Failed to load blogger detail data", e);
        alert("博主数据提取失败，请检查数据库记录。");
    }
}

// 异步加载博主物理蒸馏文件并进行渲染
async function loadBloggerPhysicalFiles(bloggerName) {
    const reportBtn = document.getElementById("btn-open-ai-report");
    if (reportBtn) {
        reportBtn.removeAttribute('data-report-url');
        reportBtn.style.opacity = '0.45';
    }

    const skillContent = document.getElementById("ai-skill-content");
    const skillEmpty = document.getElementById("ai-skill-empty");
    skillContent.innerHTML = "";
    skillContent.style.display = "none";
    skillEmpty.style.display = "block";
    skillEmpty.querySelector("p").innerHTML = `未检测到该博主的创作指南 <code>SKILL.md</code> 文件（需生成并放置于 <code>output/${bloggerName}_创作指南.skill/</code> 目录中）。`;

    const soulContent = document.getElementById("ai-soul-content");
    const soulEmpty = document.getElementById("ai-soul-empty");
    soulContent.innerHTML = "";
    soulContent.style.display = "none";
    soulEmpty.style.display = "block";
    soulEmpty.querySelector("p").innerHTML = `未检测到该博主的灵魂底稿 <code>SOUL.md</code> 文件（需生成并放置于 <code>output/${bloggerName}_创作指南.skill/</code> 目录中）。`;

    try {
        const res = await fetch(`${API_BASE}/api/bloggers/${encodeURIComponent(bloggerName)}/files_status`);
        const json = await res.json();
        if (json.status === "success") {
            const files = json.data;

            // 1. AI 蒸馏报告
            if (files.report && files.report.exists) {
                if (reportBtn) {
                    reportBtn.setAttribute('data-report-url', files.report.url);
                    reportBtn.style.opacity = '1';
                }
            }

            // 2. 创作指南 (SKILL.md)
            if (files.skill && files.skill.exists) {
                try {
                    const txtRes = await fetch(files.skill.url);
                    const markdownText = await txtRes.text();
                    if (window.marked && typeof window.marked.parse === "function") {
                        skillContent.innerHTML = window.marked.parse(markdownText);
                    } else {
                        skillContent.innerHTML = formatMarkdownFallback(markdownText);
                    }
                    skillContent.dataset.rawMd = markdownText;
                    skillContent.style.display = "block";
                    skillEmpty.style.display = "none";
                } catch (err) {
                    console.error("Failed to load SKILL.md", err);
                }
            }

            // 3. 灵魂底稿 (SOUL.md)
            if (files.soul && files.soul.exists) {
                try {
                    const txtRes = await fetch(files.soul.url);
                    const markdownText = await txtRes.text();
                    if (window.marked && typeof window.marked.parse === "function") {
                        soulContent.innerHTML = window.marked.parse(markdownText);
                    } else {
                        soulContent.innerHTML = formatMarkdownFallback(markdownText);
                    }
                    soulContent.dataset.rawMd = markdownText;
                    soulContent.style.display = "block";
                    soulEmpty.style.display = "none";
                } catch (err) {
                    console.error("Failed to load SOUL.md", err);
                }
            }
        }
    } catch (e) {
        console.error("Failed to check blogger files status", e);
    }
}

// 降级 Markdown 解析器，以防 marked 库无法加载
function formatMarkdownFallback(text) {
    if (!text) return "";
    let html = text
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;");
    
    html = html.replace(/^# (.*$)/gim, '<h1>$1</h1>')
               .replace(/^## (.*$)/gim, '<h2>$1</h2>')
               .replace(/^### (.*$)/gim, '<h3>$1</h3>')
               .replace(/^#### (.*$)/gim, '<h4>$1</h4>');
               
    html = html.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
    html = html.replace(/`(.*?)`/g, '<code>$1</code>');
    html = html.replace(/^\s*-\s+(.*$)/gim, '<li>$1</li>');
    html = html.replace(/\n/g, "<br>");
    
    return html;
}

function renderBloggerProfileHeader(b) {
    const container = document.getElementById("detail-profile-header");
    container.innerHTML = `
        <div class="profile-meta">
            <span class="section-label">CREATOR INSIGHTS</span>
            <h2>对标账号：${b.name}</h2>
        </div>
        <div class="profile-stats">
            <div class="profile-stat-box">
                <span class="profile-stat-val">${b.total_notes}条</span>
                <span class="profile-stat-lbl">笔记数</span>
            </div>
            <div class="profile-stat-box">
                <span class="profile-stat-val" style="font-family: var(--font-serif);">${b.avg_likes.toLocaleString()}</span>
                <span class="profile-stat-lbl">均赞</span>
            </div>
            <div class="profile-stat-box">
                <span class="profile-stat-val">${b.avg_collects.toLocaleString()}</span>
                <span class="profile-stat-lbl">均收藏</span>
            </div>
            <div class="profile-stat-box">
                <span class="profile-stat-val">${b.avg_comments.toLocaleString()}</span>
                <span class="profile-stat-lbl">均评论</span>
            </div>
        </div>
    `;
}

function renderBloggerOverviewTab(data) {
    const b = data.blogger;
    const dist = data.distilled;
    const statsContainer = document.getElementById("detail-stats-list");
    
    let collectLikeRatio = "0.0%";
    if (b.total_likes > 0) {
        collectLikeRatio = `${(b.total_collects / b.total_likes * 100).toFixed(1)}%`;
    }

    const struct = dist.structure_info || {};
    const freq = dist.frequency_info || {};

    statsContainer.innerHTML = `
        <table class="magazine-table" style="margin-top: 0.5rem;">
            <tbody>
                <tr><td>视频占比</td><td><strong>${b.video_count} 条 (${b.total_notes > 0 ? (b.video_count/b.total_notes*100).toFixed(0) : 0}%)</strong></td></tr>
                <tr><td>图文占比</td><td><strong>${b.normal_count} 条 (${b.total_notes > 0 ? (b.normal_count/b.total_notes*100).toFixed(0) : 0}%)</strong></td></tr>
                <tr><td>总获赞数</td><td><strong>${b.total_likes.toLocaleString()}</strong></td></tr>
                <tr><td>总收藏数</td><td><strong>${b.total_collects.toLocaleString()}</strong></td></tr>
                <tr><td>总评论数</td><td><strong>${b.total_comments.toLocaleString()}</strong></td></tr>
                <tr><td>藏赞比 (互动深度)</td><td style="color: var(--accent-primary)"><strong>${collectLikeRatio}</strong></td></tr>
                <tr><td>平均正文长度</td><td><strong>${struct.avg_length || 0} 字</strong></td></tr>
                <tr><td>发布频率特征</td><td><strong>${freq.pattern || "暂无数据"}</strong></td></tr>
            </tbody>
        </table>
    `;

    renderCategoryChart(dist.category_stats);
}

function renderCategoryChart(catStats) {
    const ctx = document.getElementById("categoryChart").getContext("2d");
    
    if (categoryChartInstance) {
        categoryChartInstance.destroy();
    }

    const categories = Object.keys(catStats || {});
    const counts = categories.map(c => catStats[c].count);
    const avgLikes = categories.map(c => catStats[c].avg_likes);

    const isDark = document.documentElement.getAttribute("data-theme") === "dark";
    const textThemeColor = isDark ? "#d9d8d6" : "#2b2a29";
    const accentThemeColor = isDark ? "#c0a068" : "#8a3c2c";
    const borderThemeColor = isDark ? "rgba(255, 255, 255, 0.1)" : "rgba(0, 0, 0, 0.08)";

    categoryChartInstance = new Chart(ctx, {
        type: "bar",
        data: {
            labels: categories,
            datasets: [
                {
                    label: "发布篇数",
                    data: counts,
                    backgroundColor: isDark ? "rgba(192, 160, 104, 0.3)" : "rgba(138, 60, 44, 0.2)",
                    borderColor: accentThemeColor,
                    borderWidth: 1,
                    yAxisID: "y-count"
                },
                {
                    label: "平均点赞",
                    data: avgLikes,
                    type: "line",
                    borderColor: isDark ? "#e6cfb3" : "#2b2a29",
                    borderWidth: 2,
                    pointBackgroundColor: accentThemeColor,
                    yAxisID: "y-likes"
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    labels: {
                        color: textThemeColor,
                        font: { family: "Outfit, sans-serif" }
                    }
                }
            },
            scales: {
                x: {
                    grid: { color: borderThemeColor },
                    ticks: { color: textThemeColor, font: { family: "Outfit, sans-serif" } }
                },
                "y-count": {
                    type: "linear",
                    position: "left",
                    grid: { color: borderThemeColor },
                    ticks: { color: textThemeColor, font: { family: "Outfit, sans-serif" } },
                    title: { display: true, text: "发布数量 (条)", color: textThemeColor }
                },
                "y-likes": {
                    type: "linear",
                    position: "right",
                    grid: { drawOnChartArea: false },
                    ticks: { color: textThemeColor, font: { family: "Outfit, sans-serif" } },
                    title: { display: true, text: "均赞表现", color: textThemeColor }
                }
            }
        }
    });

    const insightTextContainer = document.getElementById("category-insight-text");
    if (categories.length > 0) {
        let bestCat = categories[0];
        let mostCat = categories[0];
        categories.forEach(c => {
            if (catStats[c].avg_likes > catStats[bestCat].avg_likes) bestCat = c;
            if (catStats[c].count > catStats[mostCat].count) mostCat = c;
        });

        let insightHtml = `<strong>内容倾向与红利洞察</strong>：该博主产量最高的领域是「${mostCat}」，占了总发布量的 ${catStats[mostCat].pct}%。`;
        if (bestCat !== mostCat) {
            insightHtml += ` 然而，均赞效果最佳的却并非该领域，而是「${bestCat}」（均赞达 ${catStats[bestCat].avg_likes.toLocaleString()}）。这是一个高产出红利地带，适合逆向切入或重点投入。`;
        } else {
            insightHtml += ` 该领域在均赞表现上也极为突出，证明该内容心智定位极准，属于高护城河赛道。`;
        }
        insightTextContainer.innerHTML = insightHtml;
    } else {
        insightTextContainer.innerHTML = "尚未提取到合规的分类统计。";
    }
}

function refreshCategoryChartTheme() {
    if (activeBloggerName && categoryChartInstance) {
        const isDark = document.documentElement.getAttribute("data-theme") === "dark";
        const textThemeColor = isDark ? "#d9d8d6" : "#2b2a29";
        const borderThemeColor = isDark ? "rgba(255, 255, 255, 0.1)" : "rgba(0, 0, 0, 0.08)";
        const accentThemeColor = isDark ? "#c0a068" : "#8a3c2c";
        const bgBarColor = isDark ? "rgba(192, 160, 104, 0.3)" : "rgba(138, 60, 44, 0.2)";

        categoryChartInstance.options.plugins.legend.labels.color = textThemeColor;
        categoryChartInstance.options.scales.x.grid.color = borderThemeColor;
        categoryChartInstance.options.scales.x.ticks.color = textThemeColor;
        categoryChartInstance.options.scales["y-count"].grid.color = borderThemeColor;
        categoryChartInstance.options.scales["y-count"].ticks.color = textThemeColor;
        categoryChartInstance.options.scales["y-count"].title.color = textThemeColor;
        categoryChartInstance.options.scales["y-likes"].ticks.color = textThemeColor;
        categoryChartInstance.options.scales["y-likes"].title.color = textThemeColor;
        
        categoryChartInstance.data.datasets[0].backgroundColor = bgBarColor;
        categoryChartInstance.data.datasets[0].borderColor = accentThemeColor;
        categoryChartInstance.data.datasets[1].pointBackgroundColor = accentThemeColor;
        categoryChartInstance.data.datasets[1].borderColor = isDark ? "#e6cfb3" : "#2b2a29";

        categoryChartInstance.update();
    }
}

function renderBloggerWritingTab(dist) {
    const titleContainer = document.getElementById("detail-title-patterns");
    const patterns = dist.title_patterns || {};
    
    if (Object.keys(patterns).length === 0) {
        titleContainer.innerHTML = `<div class="lead-text" style="font-style: italic;">未识别出标题公式。</div>`;
    } else {
        titleContainer.innerHTML = "";
        Object.keys(patterns).forEach(pname => {
            const item = patterns[pname];
            const div = document.createElement("div");
            div.className = "pattern-detail-item";
            
            let examplesHtml = "";
            if (item.examples) {
                item.examples.forEach(ex => {
                    examplesHtml += `<div class="pattern-example-item">“${ex}”</div>`;
                });
            }
            
            div.innerHTML = `
                <div class="pattern-header">
                    <span class="pattern-name">${pname}标题</span>
                    <span class="pattern-pct">${item.pct}%</span>
                </div>
                ${examplesHtml}
            `;
            titleContainer.appendChild(div);
        });
    }

    const styleContainer = document.getElementById("detail-style-cta");
    const struct = dist.structure_info || {};
    const emoji = dist.emoji_info || {};
    const cta = dist.cta_info || {};

    let ctaRows = "";
    if (Object.keys(cta).length > 0) {
        Object.keys(cta).forEach(k => {
            ctaRows += `<tr><td>引导类型：${k}</td><td>已使用 ${cta[k].count} 次 (${cta[k].pct}%)</td></tr>`;
        });
    } else {
        ctaRows = `<tr><td colspan="2" style="font-style: italic; color: var(--ink-tertiary)">未检测到明显的行动号召 (CTA) 词汇</td></tr>`;
    }

    let emojiText = "不常使用";
    if (emoji.emoji_usage_pct > 60) {
        emojiText = `重度使用 (${emoji.emoji_usage_pct}%)`;
    } else if (emoji.emoji_usage_pct > 20) {
        emojiText = `适度点缀 (${emoji.emoji_usage_pct}%)`;
    }

    styleContainer.innerHTML = `
        <h4 class="aside-title" style="margin-bottom: 0.5rem; font-size: 1.1rem;">排版与字数特征</h4>
        <table class="magazine-table" style="margin-bottom: 2rem;">
            <tbody>
                <tr><td>短文倾向 (&lt;200字)</td><td>${struct.short_count || 0} 篇</td></tr>
                <tr><td>中等篇幅 (200-500字)</td><td>${struct.medium_count || 0} 篇</td></tr>
                <tr><td>长文深度分析 (&gt;500字)</td><td>${struct.long_count || 0} 篇</td></tr>
                <tr><td>使用了小标题/数字小标</td><td>${struct.has_number_heading || 0} 篇</td></tr>
                <tr><td>使用了列表分点符</td><td>${struct.has_list_count || 0} 篇</td></tr>
                <tr><td>Emoji 视觉表情使用率</td><td><strong>${emojiText}</strong></td></tr>
            </tbody>
        </table>
        
        <h4 class="aside-title" style="margin-bottom: 0.5rem; font-size: 1.1rem;">互动引导倾向</h4>
        <table class="magazine-table">
            <tbody>
                ${ctaRows}
            </tbody>
        </table>
    `;
}

// 加载 TOP10 笔记列表，并绑定行内评论展开交互
async function loadBloggerNotesList(bloggerName) {
    const tbody = document.querySelector("#table-top-notes tbody");
    tbody.innerHTML = `<tr><td colspan="8" class="lead-text">读取笔记中...</td></tr>`;

    try {
        const res = await fetch(`${API_BASE}/api/bloggers/${encodeURIComponent(bloggerName)}/notes?limit=10`);
        const data = await res.json();

        tbody.innerHTML = "";
        data.forEach((note, index) => {
            const tr = document.createElement("tr");
            tr.setAttribute("id", `note-row-${note.id}`);
            
            tr.innerHTML = `
                <td><strong>${index + 1}</strong></td>
                <td><div style="font-weight: 500; font-family: var(--font-serif);">${note.title}</div></td>
                <td><span style="font-size: 0.8rem; background-color: var(--bg-secondary); padding: 0.15rem 0.4rem;">${note.type === 'video' ? '视频' : '图文'}</span></td>
                <td style="color: var(--accent-primary); font-family: var(--font-serif);">${note.likes.toLocaleString()}</td>
                <td>${note.collects.toLocaleString()}</td>
                <td>${note.comments.toLocaleString()}</td>
                <td><span class="k-card-niche" style="font-size: 0.7rem; border-color: var(--ink-tertiary); color: var(--ink-secondary);">${note.category}</span></td>
                <td>
                    <button class="btn-text" style="color: var(--accent-primary); font-weight: 600;" onclick="toggleCommentsDrawer('${note.id}')">查看热评</button>
                </td>
            `;
            tbody.appendChild(tr);

            // 脱敏热评内嵌折叠行 (Inline comments drawer row)
            const commentTr = document.createElement("tr");
            commentTr.className = "row-comments-drawer";
            commentTr.setAttribute("id", `comments-drawer-${note.id}`);
            
            // 格式化正文/转录状态
            let descHtml = "";
            const isUrl = note.desc && (note.desc.startsWith("http://") || note.desc.startsWith("https://"));
            const isFailed = note.desc && note.desc.startsWith("[转录失败]");
            if (isUrl) {
                descHtml = `<span style="color: var(--ink-secondary); font-size: 0.82rem; font-style: italic;">⏳ 视频已导入，后台语音转录队列正在排队处理中... (直链: <a href="${note.desc}" target="_blank" style="color: var(--accent-primary); text-decoration: underline;">在新窗口播放</a>)</span>`;
            } else if (isFailed) {
                const cleanUrl = note.desc.includes("http") ? note.desc.substring(note.desc.indexOf("http")) : "#";
                descHtml = `<span style="color: var(--accent-primary); font-size: 0.82rem; font-style: italic;">❌ 语音转译失败 (Whisper 服务繁忙)。原视频链接: <a href="${cleanUrl}" target="_blank" style="color: var(--accent-primary); text-decoration: underline;">点击去原视频播放</a></span>`;
            } else {
                descHtml = note.desc || "无描述文本";
            }

            let commentsHtml = "";
            const commentsList = note.comments_list || [];
            if (commentsList.length > 0) {
                commentsList.forEach(c => {
                    const isAuthorBadge = c.is_author ? ` <span class="k-card-niche" style="font-size:0.65rem; padding: 0.05rem 0.25rem;">作者回复</span>` : "";
                    commentsHtml += `
                        <div class="drawer-comment-item">
                            <span class="drawer-comment-user">${c.user}${isAuthorBadge}：</span>
                            <div class="drawer-comment-content">${c.content}</div>
                        </div>
                    `;
                });
            } else {
                commentsHtml = `<div class="lead-text" style="font-style: italic;">暂无采集到的热门评论，待后续脚本自动同步。</div>`;
            }

            commentTr.innerHTML = `
                <td colspan="8">
                    <div class="comments-drawer-inner" id="drawer-inner-${note.id}">
                        <div style="margin-bottom: 1rem; padding-bottom: 1rem; border-bottom: 1px dashed var(--border-color);">
                            <h4 class="comments-drawer-title" style="margin-bottom: 0.4rem;">作品文案 / 视频语音转录</h4>
                            <p style="font-size: 0.85rem; line-height: 1.6; color: var(--ink-secondary); white-space: pre-wrap; margin-bottom: 0;">
                                ${descHtml}
                            </p>
                        </div>
                        <h4 class="comments-drawer-title">脱敏热门评论与作者互动监控</h4>
                        <div class="drawer-comments-box">
                            ${commentsHtml}
                        </div>
                    </div>
                </td>
            `;
            tbody.appendChild(commentTr);
        });
    } catch (e) {
        tbody.innerHTML = `<tr><td colspan="8" style="color: var(--accent-primary)">笔记列表加载失败</td></tr>`;
    }
}

// 折叠展开评论交互 (GSAP 驱动平滑下滑展开)
function toggleCommentsDrawer(noteId) {
    const innerDiv = document.getElementById(`drawer-inner-${noteId}`);
    if (!innerDiv) return;

    const isVisible = window.getComputedStyle(innerDiv).display !== "none";

    if (isVisible) {
        gsap.to(innerDiv, {
            opacity: 0,
            y: -10,
            duration: 0.25,
            ease: "power2.in",
            onComplete: () => {
                innerDiv.style.display = "none";
            }
        });
    } else {
        innerDiv.style.display = "block";
        gsap.fromTo(innerDiv, 
            { opacity: 0, y: -10 },
            { opacity: 1, y: 0, duration: 0.35, ease: "power2.out" }
        );
    }
}

function renderBloggerCognitiveTab(dist) {
    const container = document.getElementById("detail-cognitive-list");
    const candidates = dist.opinion_candidates || [];

    if (candidates.length === 0) {
        container.innerHTML = `<div class="lead-text" style="font-style: italic;">该博主正文中未提取到明确的断言或核心认知观点句。</div>`;
        return;
    }

    container.innerHTML = "";
    candidates.forEach((c, index) => {
        const div = document.createElement("div");
        div.className = "cognitive-item";
        div.innerHTML = `
            <div class="cognitive-quote">“${c.sentence}”</div>
            <div class="cognitive-meta">
                <span class="cognitive-badge">${c.match_type}</span>
                来源笔记：《${c.source_title}》 | 互动：${parseInt(c.source_likes).toLocaleString()} 赞
            </div>
        `;
        container.appendChild(div);
    });

    if (!window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
        gsap.from(".cognitive-item", {
            opacity: 0,
            x: -15,
            duration: 0.4,
            stagger: 0.05,
            ease: "power2.out"
        });
    }
}

function switchBloggerDetailTab(detailTabId) {
    document.querySelectorAll(".inner-tab").forEach(tab => tab.classList.remove("active"));
    document.querySelectorAll(".detail-tab-section").forEach(sec => sec.classList.remove("active"));

    document.querySelector(`.inner-tab[data-detail-tab="${detailTabId}"]`).classList.add("active");
    
    const targetSection = document.getElementById(`detail-tab-${detailTabId}`);
    targetSection.classList.add("active");

    if (!window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
        gsap.fromTo(targetSection,
            { opacity: 0 },
            { opacity: 1, duration: 0.35, ease: "power2.out" }
        );
    }
}

// 10. 行业快讯逻辑 (Feed 3)
async function loadIndustryNewsData() {
    const container = document.getElementById("news-list-container");
    container.innerHTML = `<div class="lead-text">抓取最新资讯中...</div>`;

    try {
        const res = await fetch(`${API_BASE}/api/news`);
        const data = await res.json();

        if (data.length === 0) {
            container.innerHTML = `<div class="lead-text" style="font-style: italic;">暂无快讯缓存。</div>`;
            return;
        }

        container.innerHTML = "";
        data.forEach(item => {
            const pubDate = item.published_at ? item.published_at.substring(0, 16) : "";
            
            const card = document.createElement("div");
            card.className = "news-card";
            card.innerHTML = `
                <span class="news-time">${pubDate}</span>
                <h3 class="news-title">${item.title}</h3>
                <p class="news-body">${item.content}</p>
                <div class="news-footer">
                    <span>来源：<strong class="news-source">${item.source}</strong></span>
                    <a href="${item.url}" target="_blank" class="news-link">阅读快讯原文 →</a>
                </div>
            `;
            container.appendChild(card);
        });

        if (!window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
            gsap.from(".news-card", {
                opacity: 0,
                x: -10,
                duration: 0.5,
                stagger: 0.08,
                ease: "power2.out"
            });
        }
    } catch (e) {
        container.innerHTML = `<div class="lead-text" style="color: var(--accent-primary);">无法连接行业资讯库。</div>`;
    }
}

// 11. 全网热搜逻辑 (Feed 4)
async function loadTrendingTopicsData() {
    const container = document.getElementById("trending-list-container");
    container.innerHTML = `<div class="lead-text">查询热搜中...</div>`;

    try {
        const res = await fetch(`${API_BASE}/api/trending`);
        const data = await res.json();

        if (data.length === 0) {
            container.innerHTML = `<div class="lead-text" style="font-style: italic;">暂无今日流量热词。</div>`;
            return;
        }

        container.innerHTML = "";
        data.forEach((item, index) => {
            const div = document.createElement("div");
            div.className = "trending-item";
            div.innerHTML = `
                <div class="trending-item-left">
                    <span class="trending-rank">${index + 1}</span>
                    <a href="${item.url}" target="_blank" class="trending-title">${item.title}</a>
                </div>
                <div style="display: flex; gap: 0.5rem; align-items: center;">
                    <span style="font-size: 0.75rem; color: var(--ink-secondary); font-weight: 500;">${item.source}</span>
                    <span class="trending-heat">${item.heat}</span>
                </div>
            `;
            container.appendChild(div);
        });

        if (!window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
            gsap.from(".trending-item", {
                opacity: 0,
                y: 10,
                duration: 0.4,
                stagger: 0.05,
                ease: "power2.out"
            });
        }
    } catch (e) {
        container.innerHTML = `<div class="lead-text" style="color: var(--accent-primary);">流量热搜缓存获取失败。</div>`;
    }
}

// ==================================================================
// 17. 系统设置与任务队列相关前端逻辑
// ==================================================================
let activeConsoleTaskId = null;
let consolePollInterval = null;

async function loadSettingsPageData() {
    // 加载参数配置
    try {
        const res = await fetch(`${API_BASE}/api/settings`);
        const settings = await res.json();
        
        document.getElementById("setting-whisper-url").value = settings.whisper_url || "";
        document.getElementById("setting-whisper-model").value = settings.whisper_model || "medium";
        document.getElementById("setting-max-videos").value = settings.max_videos || 5;
        document.getElementById("setting-transcribe-interval").value = settings.transcribe_interval || 5;
        document.getElementById("setting-headless").checked = settings.headless !== false;
        document.getElementById("setting-enable-transcribe").checked = settings.enable_transcribe !== false;
    } catch (e) {
        console.error("加载系统设置失败:", e);
        showToast("加载系统设置参数失败", "error");
    }
}

async function loadLogsPageData() {
    // 加载队列任务列表
    await loadSettingsPageTasks();
}

let currentTaskTab = "sync";

async function loadSettingsPageTasks() {
    try {
        const url = currentTaskTab === "sync" ? `${API_BASE}/api/crawl/tasks` : `${API_BASE}/api/transcribe/tasks`;
        const res = await fetch(url);
        const tasks = await res.json();
        
        const tbody = document.getElementById("queue-tasks-body");
        if (!tbody) return;
        
        const headerEl = document.getElementById("queue-task-header-target");
        if (headerEl) {
            headerEl.textContent = currentTaskTab === "sync" ? "目标博主" : "转录目标/视频";
        }
        
        if (tasks.length === 0) {
            tbody.innerHTML = `<tr><td colspan="4" style="text-align: center; color: var(--ink-secondary);">暂无任务记录</td></tr>`;
            return;
        }
        
        let hasActiveTasks = false;
        
        tbody.innerHTML = tasks.map(t => {
            let statusText = "未知";
            let badgeClass = "queued";
            if (t.status === "queued") { statusText = "排队中"; badgeClass = "queued"; hasActiveTasks = true; }
            else if (t.status === "running") { statusText = "进行中"; badgeClass = "running"; hasActiveTasks = true; }
            else if (t.status === "success") { statusText = "成功"; badgeClass = "success"; }
            else if (t.status === "failed") { statusText = "失败"; badgeClass = "failed"; }
            
            const dateStr = t.created_at ? new Date(t.created_at).toLocaleString() : "—";
            
            let displayBlogger = "";
            if (currentTaskTab === "sync") {
                displayBlogger = `<strong>${t.blogger === "all" ? "全部博主" : t.blogger}</strong>`;
            } else {
                const cleanTitle = t.title ? (t.title.length > 25 ? t.title.substring(0, 25) + "..." : t.title) : "无标题";
                displayBlogger = `<div style="font-size:0.75rem; color:var(--ink-secondary); font-weight:normal; line-height:1.2;">${t.blogger}</div><strong style="line-height:1.3; display:block; margin-top:0.15rem;">${cleanTitle}</strong>`;
            }
            
            const isSelected = activeConsoleTaskId === t.id;
            const btnStyle = isSelected ? "border-color: var(--accent-primary); color: var(--accent-primary);" : "";
            
            return `
                <tr>
                    <td>${displayBlogger}</td>
                    <td><span class="status-badge ${badgeClass}">${statusText}</span></td>
                    <td style="font-family: var(--font-mono); font-size: 0.72rem;">${dateStr}</td>
                    <td style="text-align: right;">
                        <button class="btn-text btn-view-log" data-id="${t.id}" style="padding: 0.15rem 0.45rem; font-size: 0.72rem; ${btnStyle}">查看日志</button>
                    </td>
                </tr>
            `;
        }).join("");
        
        // 绑定“查看日志”按钮事件
        tbody.querySelectorAll(".btn-view-log").forEach(btn => {
            btn.addEventListener("click", (e) => {
                const taskId = e.currentTarget.getAttribute("data-id");
                // 移除所有按钮的激活高亮
                tbody.querySelectorAll(".btn-view-log").forEach(b => {
                    b.style.borderColor = "";
                    b.style.color = "";
                });
                e.currentTarget.style.borderColor = "var(--accent-primary)";
                e.currentTarget.style.color = "var(--accent-primary)";
                selectConsoleTask(taskId);
            });
        });
        
        // 如果有正在运行的任务，且没有全局的轮询机制，就在日志页每 3 秒刷新一次列表
        if (hasActiveTasks && currentTab === "logs") {
            setTimeout(loadSettingsPageTasks, 3000);
        }
    } catch (e) {
        console.error("加载任务队列失败:", e);
    }
}

function selectConsoleTask(taskId) {
    activeConsoleTaskId = taskId;
    
    // 更新控制台标题
    const titleEl = document.getElementById("console-task-title");
    if (titleEl) {
        titleEl.textContent = `实时控制台日志 (任务 ID: ${taskId.substring(0, 8)}...)`;
    }
    
    // 立即拉取一次日志并开启日志轮询
    pollConsoleLog(taskId);
}

function pollConsoleLog(taskId) {
    if (consolePollInterval) {
        clearInterval(consolePollInterval);
        consolePollInterval = null;
    }
    
    const consoleContent = document.getElementById("settings-console-content");
    if (!consoleContent) return;
    
    const fetchLog = () => {
        // 如果选定的任务变了，或不再日志页，停止该轮询
        if (activeConsoleTaskId !== taskId || currentTab !== "logs") {
            if (consolePollInterval) {
                clearInterval(consolePollInterval);
                consolePollInterval = null;
            }
            return;
        }
        
        fetch(`${API_BASE}/api/crawl/status/${taskId}`)
            .then(res => res.json())
            .then(json => {
                if (json.status === "error") {
                    consoleContent.textContent = `❌ 获取日志错误: ${json.message}`;
                    if (consolePollInterval) clearInterval(consolePollInterval);
                    return;
                }
                
                // 填充日志
                consoleContent.textContent = json.logs || "等待日志输出...\n";
                consoleContent.scrollTop = consoleContent.scrollHeight;
                
                // 更新当前运行步骤/卡住位置看板
                const stepBox = document.getElementById("console-step-box");
                const stepText = document.getElementById("console-step-text");
                const screenshotTitle = document.getElementById("console-screenshots-title");
                const screenshotDesc = document.getElementById("console-screenshots-desc");
                
                if (stepBox && stepText) {
                    if (json.current_step) {
                        stepText.textContent = json.current_step;
                        stepBox.style.display = "block";
                        
                        // 动态改变截图提示，指导用户进行登录
                        if (json.current_step.includes("扫码登录") && screenshotTitle && screenshotDesc) {
                            screenshotTitle.textContent = "⚠️ 请使用抖音 APP 扫码登录";
                            screenshotTitle.style.color = "var(--accent-primary)";
                            screenshotDesc.textContent = "系统检测到未登录状态。请使用手机抖音 APP 扫描下方二维码完成登录。登录完成后系统将自动刷新页面验证并继续抓取。";
                        } else if (screenshotTitle && screenshotDesc) {
                            screenshotTitle.textContent = "异常/验证码截图排查";
                            screenshotTitle.style.color = "";
                            screenshotDesc.textContent = "如果在网页爬取时遇到滑动验证码或操作报错，下方将显示对应的浏览器截图。请在服务器/浏览器窗口中配合操作，或根据截图更新规则。";
                        }
                    } else {
                        stepBox.style.display = "none";
                    }
                }
                
                // 渲染截图
                const screenshotBox = document.getElementById("console-screenshots-box");
                const screenshotContainer = document.getElementById("console-screenshots-container");
                if (screenshotBox && screenshotContainer) {
                    if (json.screenshots && json.screenshots.length > 0) {
                        screenshotContainer.innerHTML = json.screenshots.map(url => {
                            const basename = url.split("/").pop();
                            return `
                                <div style="border: 1px solid var(--ink-primary); padding: 0.25rem; background-color: var(--bg-secondary); text-align: center;">
                                    <a href="${url}" target="_blank" title="在新标签页中打开完整截图">
                                        <img src="${url}" alt="${basename}" style="width: 100%; height: auto; display: block; border: 1px solid var(--ink-secondary);" />
                                    </a>
                                    <div style="font-family: var(--font-mono); font-size: 0.65rem; margin-top: 0.25rem; word-break: break-all; color: var(--ink-secondary);">${basename}</div>
                                </div>
                            `;
                        }).join("");
                        screenshotBox.style.display = "block";
                    } else {
                        screenshotContainer.innerHTML = "";
                        screenshotBox.style.display = "none";
                    }
                }
                
                // 如果任务已经结束，则停止轮询，并立即触发一次左侧任务列表刷新
                if (json.status === "success" || json.status === "failed") {
                    if (consolePollInterval) {
                        clearInterval(consolePollInterval);
                        consolePollInterval = null;
                    }
                    loadSettingsPageTasks();
                }
            })
            .catch(err => {
                console.error("加载日志错误:", err);
            });
    };
    
    // 立即执行一次
    fetchLog();
    
    // 每 1.5 秒更新一次
    consolePollInterval = setInterval(fetchLog, 1500);
}

// 提交系统参数设置
async function handleSystemSettingsSubmit(e) {
    e.preventDefault();
    const whisper_url = document.getElementById("setting-whisper-url").value.trim();
    const whisper_model = document.getElementById("setting-whisper-model").value;
    const max_videos = parseInt(document.getElementById("setting-max-videos").value);
    const transcribe_interval = parseInt(document.getElementById("setting-transcribe-interval").value);
    const headless = document.getElementById("setting-headless").checked;
    const enable_transcribe = document.getElementById("setting-enable-transcribe").checked;
    
    try {
        const res = await fetch(`${API_BASE}/api/settings`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ whisper_url, whisper_model, max_videos, transcribe_interval, headless, enable_transcribe })
        });
        const json = await res.json();
        if (json.status === "success") {
            showToast("系统设置已成功保存！", "success");
        } else {
            showToast(`保存失败: ${json.detail || "未知错误"}`, "error");
        }
    } catch (err) {
        showToast(`请求后端出错: ${err.message}`, "error");
    }
}

// 一键更新全部博主 (提交后跳转至日志页方便监控)
function handleSyncAllClick() {
    showToast("已开始向队列提交全博主同步更新任务...", "info");
    fetch(`${API_BASE}/api/crawl/run?blogger=all`, { method: "POST" })
        .then(res => res.json())
        .then(json => {
            if (json.status === "success" && json.task_id) {
                showToast("全博主同步任务已成功加入队列！正在转至任务日志面...", "success");
                // 延迟切换标签以给用户时间看到 Toast
                setTimeout(() => {
                    switchTab("logs");
                    loadSettingsPageTasks();
                    selectConsoleTask(json.task_id);
                }, 1000);
            } else {
                throw new Error(json.message || "任务创建失败");
            }
        })
        .catch(err => {
            showToast(`同步失败: ${err.message}`, "error");
        });
}

// 清除已完成任务历史
async function handleClearHistoryClick() {
    try {
        const res = await fetch(`${API_BASE}/api/crawl/clear`, { method: "POST" });
        const json = await res.json();
        if (json.status === "success") {
            showToast("已清除所有已完成任务历史记录", "success");
            loadSettingsPageTasks();
        } else {
            showToast("清除任务历史记录失败", "error");
        }
    } catch (e) {
        showToast("连接后端出错", "error");
    }
}

// 立即触发后台数据库扫描转录任务
async function handleTranscribeNowClick() {
    showToast("已向后台发送立即扫描数据库转录指令...", "info");
    try {
        const res = await fetch(`${API_BASE}/api/transcribe/trigger`, { method: "POST" });
        const json = await res.json();
        if (json.status === "success") {
            const count = json.count || 0;
            if (count > 0) {
                showToast(`后台扫描完成！检测到 ${count} 个视频待转录，已启动处理流程，正在转至监控...`, "success");
                
                // 仅在有任务时自动切换到日志监控页面的转录选项卡
                setTimeout(() => {
                    switchTab("logs");
                    currentTaskTab = "transcribe";
                    const btnTabSync = document.getElementById("task-tab-sync");
                    const btnTabTranscribe = document.getElementById("task-tab-transcribe");
                    if (btnTabSync && btnTabTranscribe) {
                        btnTabTranscribe.style.color = "var(--accent-primary)";
                        btnTabTranscribe.style.borderBottom = "2px solid var(--accent-primary)";
                        btnTabSync.style.color = "var(--ink-secondary)";
                        btnTabSync.style.borderBottom = "none";
                    }
                    loadSettingsPageTasks();
                }, 1000);
            } else {
                showToast("后台扫描已完成：当前数据库中没有待转录视频直链（所有视频已转录完成或无视频数据）。", "info");
            }
        } else {
            showToast(`唤醒转录失败: ${json.message}`, "error");
        }
    } catch (e) {
        showToast(`请求后端出错: ${e.message}`, "error");
    }
}
