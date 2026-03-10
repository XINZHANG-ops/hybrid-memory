
        let projects = [];
        let currentProject = '';
        let currentPanel = 'overview';
        let currentSession = '';
        let sessionsData = [];
        let interactionsData = [];  // 存储权限请求和用户选择等交互记录
        let expandedInteractions = new Set();  // 记录已展开的交互面板 ID
        // 全局配置（从后端加载）
        let appConfig = {
            summary_max_chars_total: 8000,
            search_result_preview_length: 500,
            dashboard_refresh_interval: 5000,
        };

        function showPanel(id) {
            currentPanel = id;
            document.querySelectorAll('.panel').forEach(p => {
                p.classList.remove('active');
                p.style.display = 'none';
            });
            const panel = document.getElementById(id);
            panel.classList.add('active');
            // Messages panel needs flex display
            if (id === 'messages') {
                panel.style.display = 'flex';
            } else {
                panel.style.display = 'block';
            }
            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            event.target.classList.add('active');
            // Update scroll button visibility
            updateScrollButtonVisibility();
            // Auto-load data for specific panels
            if (id === 'knowledge' && currentProject) {
                loadKnowledge();
            } else if (id === 'decisions' && currentProject) {
                loadDecisions();
            } else if (id === 'search' && currentProject) {
                loadVectorStats();
            } else if (id === 'logs') {
                loadLogs();
            }
        }

        function updateScrollButtonVisibility() {
            const btn = document.getElementById('scroll-to-bottom-btn');
            if (!btn) return;
            // Only show in messages panel
            if (currentPanel !== 'messages') {
                btn.style.display = 'none';
                return;
            }
            const listEl = document.getElementById('message-list');
            if (!listEl) return;
            const isNearBottom = listEl.scrollHeight - listEl.scrollTop - listEl.clientHeight < 200;
            btn.style.display = isNearBottom ? 'none' : 'block';
        }

        function onProjectChange() {
            currentProject = document.getElementById('global-project').value;
            currentSession = '';  // Reset session when project changes
            localStorage.setItem('currentProject', currentProject);
            loadProjectData();
        }

        function loadProjectData() {
            if (!currentProject) {
                document.getElementById('message-list').innerHTML = '<div class="no-project">Please select a project</div>';
                document.getElementById('context-preview').innerHTML = '<div class="no-project">Please select a project</div>';
                return;
            }
            loadSessions();
            loadMessages();
            loadVectorStats();
            loadSummaries();
            loadContext();
        }

        async function loadProjects() {
            const res = await fetch('/api/projects');
            const data = await res.json();
            projects = data.projects;
            const totals = data.totals || {};

            // 格式化 token 数量
            const formatTokens = (n) => {
                if (n >= 1000000) return (n / 1000000).toFixed(2) + 'M';
                if (n >= 1000) return (n / 1000).toFixed(1) + 'K';
                return n.toString();
            };

            // Stats
            let totalMsgs = 0, totalSums = 0;
            projects.forEach(p => {
                totalMsgs += p.messages || 0;
                totalSums += p.summaries || 0;
            });
            document.getElementById('stats').innerHTML = `
                <div class="card stat"><div class="stat-value">${projects.length}</div><div class="stat-label">Projects</div></div>
                <div class="card stat"><div class="stat-value">${totalMsgs}</div><div class="stat-label">Total Messages</div></div>
                <div class="card stat"><div class="stat-value">${totalSums}</div><div class="stat-label">Summaries</div></div>
                <div class="card stat"><div class="stat-value">${formatTokens(totals.input_tokens || 0)}</div><div class="stat-label">Input Tokens</div></div>
                <div class="card stat"><div class="stat-value">${formatTokens(totals.output_tokens || 0)}</div><div class="stat-label">Output Tokens</div></div>
                <div class="card stat" style="background: linear-gradient(135deg, #1a1a2e 0%, #2d1f3d 100%);"><div class="stat-value" style="color: #f39c12;">$${(totals.total_cost || 0).toFixed(2)}</div><div class="stat-label">Total Cost</div></div>
            `;

            // Project list
            document.getElementById('project-list').innerHTML = projects.map(p => `
                <div class="card" style="cursor: pointer;" onclick="selectProject('${p.name}')">
                    <div class="card-header"><strong>${p.name}</strong><span class="badge">${p.messages || 0} msgs</span></div>
                    <div>Sessions: ${p.sessions || 0} | Summaries: ${p.summaries || 0}</div>
                    <div style="margin-top: 6px; font-size: 12px; color: #aaa;">
                        <span title="Input tokens">📥 ${formatTokens(p.input_tokens || 0)}</span>
                        <span style="margin-left: 8px;" title="Output tokens">📤 ${formatTokens(p.output_tokens || 0)}</span>
                        <span style="margin-left: 8px; color: #f39c12;" title="Cost">💰 $${(p.cost || 0).toFixed(4)}</span>
                    </div>
                </div>
            `).join('');

            // Global dropdown
            const opts = projects.map(p => `<option value="${p.name}" ${p.name === currentProject ? 'selected' : ''}>${p.name}</option>`).join('');
            document.getElementById('global-project').innerHTML = '<option value="">-- Select Project --</option>' + opts;

            // Auto-select first or saved
            if (!currentProject && projects.length > 0) {
                const saved = localStorage.getItem('currentProject');
                if (saved && projects.find(p => p.name === saved)) {
                    currentProject = saved;
                } else {
                    currentProject = projects[0].name;
                }
                document.getElementById('global-project').value = currentProject;
            }
        }

        function selectProject(name) {
            currentProject = name;
            currentSession = '';  // Reset session when project changes
            document.getElementById('global-project').value = name;
            localStorage.setItem('currentProject', name);
            loadProjectData();
        }

        async function loadSessions() {
            if (!currentProject) return;
            const res = await fetch(`/api/projects/${currentProject}/sessions`);
            const data = await res.json();
            sessionsData = data.sessions || [];

            // 格式化时间显示（精确到秒）
            const formatTime = (ts) => {
                if (!ts) return 'Unknown';
                const d = new Date(ts);
                return d.toLocaleString('zh-CN', {month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit'});
            };

            const opts = sessionsData.map(s => {
                const timeLabel = formatTime(s.started_at);
                const activeLabel = s.is_active ? ' 🟢' : '';
                return `<option value="${s.session_id}">${timeLabel}${activeLabel}</option>`;
            }).join('');

            const selectEl = document.getElementById('msg-session');
            selectEl.innerHTML = '<option value="">All Sessions</option>' + opts;

            // 恢复之前选中的 session
            if (currentSession && sessionsData.find(s => s.session_id === currentSession)) {
                selectEl.value = currentSession;
            }
            updateSessionIdDisplay();
        }

        function onSessionChange() {
            currentSession = document.getElementById('msg-session').value;
            updateSessionIdDisplay();
            loadMessages();
        }

        function updateSessionIdDisplay() {
            const display = document.getElementById('session-id-display');
            const text = document.getElementById('session-id-text');
            if (currentSession) {
                display.style.display = 'block';
                text.textContent = currentSession;
            } else {
                display.style.display = 'none';
            }
        }

        let messagesInitialized = false;

        // 简单 Markdown 渲染
        function renderMarkdown(text) {
            let html = escapeHtml(text);
            // 代码块 ```...```
            html = html.replace(/```(\w*)\n([\s\S]*?)```/g, '<pre style="background:#1a1a2e;padding:8px;border-radius:4px;overflow-x:auto;"><code>$2</code></pre>');
            // 行内代码 `...`
            html = html.replace(/`([^`]+)`/g, '<code style="background:#1a1a2e;padding:2px 4px;border-radius:3px;">$1</code>');
            // 粗体 **...**
            html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
            // 斜体 *...*
            html = html.replace(/\*([^*]+)\*/g, '<em>$1</em>');
            return html;
        }

        // 解析 AI 消息内容，分层显示
        // 新格式：JSON 数组 [{"type": "thinking/tool/text", "content": "...", "name": "..."}]
        // 旧格式：纯文本（向后兼容）
        function parseAssistantContent(content) {
            // 尝试解析为 JSON（新格式）
            try {
                const blocks = JSON.parse(content);
                if (Array.isArray(blocks) && blocks.length > 0) {
                    return blocks.map(b => ({
                        type: b.type === 'thinking' ? 'thinking' : (b.type === 'tool' ? 'tool' : 'text'),
                        content: b.content || '',
                        name: b.name || ''
                    }));
                }
            } catch (e) {
                // 不是 JSON，使用旧的文本解析方式（向后兼容）
            }

            // 旧格式：纯文本，返回单个文本块
            return [{ type: 'text', content: content }];
        }

        function formatModelName(model) {
            if (!model) return 'Assistant';
            return model;
        }

        function getInteractionsForMessage(msgTimestamp, prevMsgTimestamp) {
            // 找到在当前消息时间戳之前、上一条消息之后的 interactions
            const msgTime = new Date(msgTimestamp).getTime();
            const prevTime = prevMsgTimestamp ? new Date(prevMsgTimestamp).getTime() : 0;
            return interactionsData.filter(i => {
                const iTime = new Date(i.timestamp).getTime();
                return iTime <= msgTime && iTime > prevTime;
            });
        }

        function toggleInteractionPanel(id) {
            const el = document.getElementById(id);
            if (!el) return;
            if (el.style.display === 'none') {
                el.style.display = 'block';
                expandedInteractions.add(id);
            } else {
                el.style.display = 'none';
                expandedInteractions.delete(id);
            }
        }

        function renderInteractions(interactions, msgId) {
            if (!interactions || interactions.length === 0) return '';
            const summary = interactions.map(i => {
                const icon = i.type === 'permission_request' ? '🔐' : '❓';
                const response = i.user_response === 'yes' ? '✓' : (i.user_response === 'no' ? '✗' : i.user_response);
                return `${icon} ${i.tool_name}: ${response}`;
            }).join(' | ');

            let detailHtml = interactions.map(i => {
                const icon = i.type === 'permission_request' ? '🔐' : '❓';
                const typeLabel = i.type === 'permission_request' ? 'Permission' : 'Choice';
                const responseColor = i.user_response === 'yes' ? '#4ade80' : (i.user_response === 'no' ? '#f87171' : '#fbbf24');
                const content = i.request_content.length > 100 ? i.request_content.substring(0, 100) + '...' : i.request_content;
                return `<div style="padding: 4px 0; border-bottom: 1px solid #333;">
                    <span style="color: #888;">${icon} ${typeLabel}</span>
                    <span style="color: #d9a04a; margin-left: 8px;">${escapeHtml(i.tool_name)}</span>
                    <span style="color: ${responseColor}; margin-left: 8px; font-weight: bold;">${escapeHtml(i.user_response)}</span>
                    <div style="color: #777; font-size: 0.85em; margin-top: 2px; font-family: monospace;">${escapeHtml(content)}</div>
                </div>`;
            }).join('');

            // 使用稳定的 ID（基于消息 ID）
            const id = 'int-msg-' + msgId;
            const isExpanded = expandedInteractions.has(id);
            return `<div style="background: #1a1a2e; border-radius: 6px; padding: 6px 10px; margin-bottom: 8px; border-left: 3px solid #9333ea; font-size: 0.85em;">
                <div style="cursor: pointer; color: #a78bfa;" onclick="toggleInteractionPanel('${id}')">
                    ⚡ ${interactions.length} interaction${interactions.length > 1 ? 's' : ''}: ${summary.length > 60 ? summary.substring(0, 60) + '...' : summary}
                </div>
                <div id="${id}" style="display: ${isExpanded ? 'block' : 'none'}; margin-top: 6px;">${detailHtml}</div>
            </div>`;
        }

        function renderAssistantMessage(m, prevTimestamp) {
            const parts = parseAssistantContent(m.content);
            const modelLabel = formatModelName(m.model);
            const interactions = getInteractionsForMessage(m.timestamp, prevTimestamp);

            let html = `<div style="display: flex; justify-content: flex-start; margin-bottom: 20px;">
                <div style="max-width: 85%; width: 100%;">
                    <div style="font-size: 0.75em; color: #e94560; margin-bottom: 8px; font-weight: bold;">${modelLabel} <span style="color: #666; font-weight: normal;">#${m.id}</span></div>`;

            // 先显示 interactions（在消息内容之前）
            html += renderInteractions(interactions, m.id);

            for (const part of parts) {
                if (part.type === 'thinking') {
                    html += `<div style="background: #1a2a3a; border-radius: 8px; padding: 10px 14px; margin-bottom: 8px; border-left: 3px solid #4a90d9;">
                        <div style="font-size: 0.7em; color: #4a90d9; margin-bottom: 4px; font-weight: bold;">💭 Thinking</div>
                        <div style="white-space: pre-wrap; word-break: break-word; line-height: 1.4; color: #9ab; font-size: 0.9em;">${renderMarkdown(part.content)}</div>
                    </div>`;
                } else if (part.type === 'tool') {
                    const toolName = part.name || 'Tool';
                    html += `<div style="background: #2a2a1a; border-radius: 8px; padding: 10px 14px; margin-bottom: 8px; border-left: 3px solid #d9a04a;">
                        <div style="font-size: 0.7em; color: #d9a04a; margin-bottom: 4px; font-weight: bold;">🔧 ${escapeHtml(toolName)}</div>
                        <div style="white-space: pre-wrap; word-break: break-word; line-height: 1.4; color: #cb9; font-size: 0.85em; font-family: monospace;">${escapeHtml(part.content)}</div>
                    </div>`;
                } else {
                    if (part.content.trim()) {
                        html += `<div style="background: #2d1f3d; border-radius: 8px; padding: 10px 14px; margin-bottom: 8px; border-left: 3px solid #e94560;">
                            <div style="white-space: pre-wrap; word-break: break-word; line-height: 1.5; color: #eee;">${renderMarkdown(part.content)}</div>
                        </div>`;
                    }
                }
            }

            html += `<div style="font-size: 0.7em; color: #666; margin-top: 4px; text-align: right;">${m.timestamp} ${m.is_summarized ? '✓ Summarized' : ''}</div>
                </div>
            </div>`;
            return html;
        }

        async function loadMessages() {
            if (!currentProject) return;
            const msgUrl = currentSession ? `/api/projects/${currentProject}/messages?session_id=${encodeURIComponent(currentSession)}` : `/api/projects/${currentProject}/messages`;
            const intUrl = currentSession ? `/api/projects/${currentProject}/interactions?session_id=${encodeURIComponent(currentSession)}` : `/api/projects/${currentProject}/interactions`;

            const [msgRes, intRes] = await Promise.all([fetch(msgUrl), fetch(intUrl)]);
            const msgData = await msgRes.json();
            const intData = await intRes.json();

            const messages = [...(msgData.messages || [])].reverse();
            interactionsData = intData.interactions || [];
            const listEl = document.getElementById('message-list');
            listEl.innerHTML = messages.map((m, idx) => {
                const prevTimestamp = idx > 0 ? messages[idx - 1].timestamp : null;
                if (m.role === 'user') {
                    return `
                    <div style="display: flex; justify-content: flex-end; margin-bottom: 20px;">
                        <div style="max-width: 80%; background: #1a3a5c; border-radius: 12px; padding: 12px 16px; border-left: 3px solid #00d9ff;">
                            <div style="font-size: 0.75em; color: #00d9ff; margin-bottom: 6px; font-weight: bold;">You <span style="color: #666; font-weight: normal;">#${m.id}</span></div>
                            <div style="white-space: pre-wrap; word-break: break-word; line-height: 1.5; color: #eee;">${escapeHtml(m.content)}</div>
                            <div style="font-size: 0.7em; color: #666; margin-top: 8px; text-align: right;">${m.timestamp} ${m.is_summarized ? '✓ Summarized' : ''}</div>
                        </div>
                    </div>`;
                } else {
                    return renderAssistantMessage(m, prevTimestamp);
                }
            }).join('') || '<p style="color: #888; text-align: center; padding: 40px;">No messages found</p>';

            // 设置滚动监听（只设置一次）
            if (!listEl.hasAttribute('data-scroll-init')) {
                listEl.setAttribute('data-scroll-init', 'true');
                listEl.onscroll = updateScrollButtonVisibility;
            }

            // 只在首次加载时滚动到底部
            if (!messagesInitialized) {
                messagesInitialized = true;
                setTimeout(() => {
                    listEl.scrollTop = listEl.scrollHeight;
                    updateScrollButtonVisibility();
                }, 100);
            } else {
                // 非首次加载时也检查一下按钮状态
                setTimeout(updateScrollButtonVisibility, 50);
            }
        }

        function scrollMessagesToBottom() {
            const listEl = document.getElementById('message-list');
            if (!listEl) return;
            listEl.scrollTop = listEl.scrollHeight;
            updateScrollButtonVisibility();
        }

        let extraSelection = [];  // 手动选择的额外总结
        let summariesData = [];
        let isEditing = false;
        let selectionDirty = false;
        let defaultInjectCount = 5;

        async function loadSummaries() {
            if (!currentProject) return;
            const [summariesRes, selectionRes, configRes] = await Promise.all([
                fetch(`/api/projects/${currentProject}/summaries`),
                fetch(`/api/projects/${currentProject}/summaries/selection`),
                fetch('/api/config')
            ]);
            const data = await summariesRes.json();
            const selData = await selectionRes.json();
            const configData = await configRes.json();
            summariesData = data.summaries || [];
            defaultInjectCount = parseInt(configData.config?.inject_summary_count || configData.defaults?.inject_summary_count || 5);
            extraSelection = selData.selected_ids || [];
            renderSummaries();
        }

        let expandedSummaries = new Set();

        function renderSummaries() {
            const allEl = document.getElementById('summary-list-all');
            const autoEl = document.getElementById('summary-list-auto');
            const extraEl = document.getElementById('summary-list-extra');
            const countEl = document.getElementById('inject-count-display');
            if (!allEl || !autoEl || !extraEl) return;

            if (countEl) countEl.textContent = defaultInjectCount;

            if (summariesData.length === 0) {
                allEl.innerHTML = '<p style="color: #666;">No summaries</p>';
                autoEl.innerHTML = '<p style="color: #666;">No summaries</p>';
                extraEl.innerHTML = '<p style="color: #666;">None</p>';
                return;
            }

            const autoIds = new Set(summariesData.slice(0, defaultInjectCount).map(s => s.id));
            const extraSet = new Set(extraSelection);

            // 左栏：显示所有总结，已添加的变深色
            allEl.innerHTML = summariesData.map(s => {
                const isAuto = autoIds.has(s.id);
                const isExtra = extraSet.has(s.id);
                const isSelected = isAuto || isExtra;
                const bgColor = isSelected ? '#0a1525' : '#16213e';
                const borderColor = isAuto ? '#00d9ff' : (isExtra ? '#e94560' : '#333');
                const labelColor = isAuto ? '#00d9ff' : (isExtra ? '#e94560' : '#888');
                const badge = isAuto ? '<span style="background:#00d9ff;color:#000;padding:1px 4px;border-radius:3px;font-size:0.7em;margin-left:5px;">AUTO</span>' : (isExtra ? '<span style="background:#e94560;color:#fff;padding:1px 4px;border-radius:3px;font-size:0.7em;margin-left:5px;">EXTRA</span>' : '');
                const isExpanded = expandedSummaries.has(s.id);
                const hasRange = s.message_range_start && s.message_range_end;
                return `
                <div class="card" style="margin-bottom: 8px; padding: 8px; background: ${bgColor}; border-left: 3px solid ${borderColor};">
                    <div style="display: flex; justify-content: space-between; align-items: center;">
                        <div style="display: flex; align-items: center; cursor: pointer;" onclick="toggleExpandSummary(${s.id})">
                            <span style="color: #666; margin-right: 5px;">${isExpanded ? '▼' : '▶'}</span>
                            <strong style="color: ${labelColor};">#${s.id}</strong>${badge}
                        </div>
                        <div style="display: flex; gap: 4px;">
                            ${!isAuto && !isExtra ? `<button onclick="event.stopPropagation();addExtra(${s.id})" style="padding: 2px 6px; font-size: 0.75em; background: #e94560;">+</button>` : ''}
                            ${isExtra ? `<button onclick="event.stopPropagation();removeExtra(${s.id})" style="padding: 2px 6px; font-size: 0.75em; background: #333;">✕</button>` : ''}
                            <button onclick="event.stopPropagation();toggleEditSummary(${s.id})" style="padding: 2px 6px; font-size: 0.75em;">Edit</button>
                            <button onclick="event.stopPropagation();regenerateSummary(${s.id})" style="padding: 2px 6px; font-size: 0.75em; background: #e94560;">Regen</button>
                            ${hasRange ? `<button onclick="event.stopPropagation();showSummaryMessages(${s.id}, ${s.message_range_start}, ${s.message_range_end})" style="padding: 2px 6px; font-size: 0.75em; background: #1f4068;">📜</button>` : ''}
                        </div>
                    </div>
                    <div style="font-size: 0.8em; color: #666; margin: 3px 0;">${s.created_at} | ${s.message_count} msgs</div>
                    <div id="summary-view-${s.id}" style="font-size: 0.85em; color: #ccc; margin-top: 5px; ${isExpanded ? '' : 'max-height: 50px; overflow: hidden;'}">${escapeHtml(s.summary_text)}</div>
                    <div id="summary-edit-${s.id}" style="display: none; margin-top: 8px;">
                        <textarea id="summary-textarea-${s.id}" style="width: 100%; min-height: 100px; background: #1a1a2e; color: #eee; border: 1px solid #333; border-radius: 5px; padding: 6px; font-size: 0.85em;">${escapeHtml(s.summary_text)}</textarea>
                        <div style="margin-top: 5px;">
                            <button onclick="saveSummaryEdit(${s.id})" style="padding: 3px 10px; font-size: 0.8em;">Save</button>
                            <button onclick="toggleEditSummary(${s.id})" style="padding: 3px 10px; font-size: 0.8em; background: #333;">Cancel</button>
                        </div>
                    </div>
                </div>`;
            }).join('');

            // 右栏 Auto 部分（旧到新）
            const autoSummaries = summariesData.slice(0, defaultInjectCount).reverse();
            autoEl.innerHTML = autoSummaries.map(s => `
                <div style="padding: 6px; margin-bottom: 4px; background: #0a1a2a; border-radius: 4px; font-size: 0.85em;">
                    <strong style="color: #00d9ff;">#${s.id}</strong>
                    <span style="color: #666; margin-left: 8px;">${s.summary_text.substring(0, 60)}...</span>
                </div>
            `).join('') || '<p style="color: #666;">None</p>';

            // 右栏 Extra 部分
            const extraSummaries = extraSelection.map(id => summariesData.find(s => s.id === id)).filter(Boolean);
            extraEl.innerHTML = extraSummaries.map(s => `
                <div class="card" data-id="${s.id}" draggable="true" ondragstart="onDragStart(event)" ondragover="onDragOver(event)" ondrop="onDrop(event)" ondragend="onDragEnd(event)" style="margin-bottom: 6px; padding: 6px; cursor: grab; border-left: 3px solid #e94560; background: #1a1a2e;">
                    <div style="display: flex; justify-content: space-between; align-items: center;">
                        <div style="display: flex; align-items: center; gap: 4px;">
                            <span style="color: #666;">⋮⋮</span>
                            <strong style="color: #e94560;">#${s.id}</strong>
                        </div>
                        <button onclick="removeExtra(${s.id})" style="padding: 2px 6px; font-size: 0.75em; background: #333;">✕</button>
                    </div>
                    <div style="font-size: 0.8em; color: #999; margin-top: 4px;">${s.summary_text.substring(0, 80)}...</div>
                </div>
            `).join('') || '<p style="color: #666;">Click + on left to add</p>';
        }

        function toggleExpandSummary(id) {
            if (expandedSummaries.has(id)) {
                expandedSummaries.delete(id);
            } else {
                expandedSummaries.add(id);
            }
            renderSummaries();
        }

        async function showSummaryMessages(summaryId, startId, endId) {
            const res = await fetch(`/api/projects/${currentProject}/messages/range?start=${startId}&end=${endId}`);
            const data = await res.json();
            const messages = data.messages || [];

            // 使用 processed_content（后端统一处理）计算哪些消息会被包含
            const maxTotal = appConfig.summary_max_chars_total;
            let totalChars = 0;
            let includedCount = 0;

            // 从后往前计算
            const reversed = [...messages].reverse();
            for (const m of reversed) {
                const contentLen = (m.processed_content || '').length;
                if (totalChars + contentLen > maxTotal) break;
                totalChars += contentLen;
                includedCount++;
            }
            const excludedCount = messages.length - includedCount;

            let html = '';
            if (excludedCount > 0) {
                html += `<div style="background: #4a3000; color: #ffcc00; padding: 10px; border-radius: 8px; margin-bottom: 15px; font-size: 0.85em;">
                    ⚠️ 前 ${excludedCount} 条消息因字符限制未包含在 summary 中（总限制 ${maxTotal} 字符）
                </div>`;
            }

            html += messages.map((m, idx) => {
                const isUser = m.role === 'user';
                const bgColor = isUser ? '#1a3a5c' : '#2d1f3d';
                const borderColor = isUser ? '#00d9ff' : '#e94560';
                const isExcluded = idx < excludedCount;
                // 使用 processed_content 作为显示内容
                const displayContent = m.processed_content || m.content.substring(0, 200) + '...';
                const isTruncated = m.processed_content && m.processed_content !== m.content;

                const excludedStyle = isExcluded ? 'opacity: 0.4;' : '';
                const excludedBadge = isExcluded ? '<span style="background: #666; color: #ccc; padding: 1px 4px; border-radius: 3px; font-size: 0.7em; margin-left: 5px;">未包含</span>' : '';
                const truncatedBadge = isTruncated && !isExcluded ? '<span style="background: #3a3a1a; color: #d9d94a; padding: 1px 4px; border-radius: 3px; font-size: 0.7em; margin-left: 5px;">已简化</span>' : '';

                const roleLabel = isUser ? 'You' : formatModelName(m.model);
                return `<div style="display: flex; justify-content: ${isUser ? 'flex-end' : 'flex-start'}; margin-bottom: 10px; ${excludedStyle}">
                    <div style="max-width: 90%; background: ${bgColor}; border-radius: 8px; padding: 10px; border-left: 3px solid ${borderColor};">
                        <div style="font-size: 0.7em; color: ${borderColor}; font-weight: bold;">${roleLabel} <span style="color: #666; font-weight: normal;">#${m.id}</span>${excludedBadge}${truncatedBadge}</div>
                        <div style="white-space: pre-wrap; word-break: break-word; font-size: 0.85em; color: #eee;">${escapeHtml(displayContent)}</div>
                    </div>
                </div>`;
            }).join('');

            document.getElementById('summary-messages-content').innerHTML = html || '<p style="color:#888;">No messages found</p>';
            document.getElementById('summary-messages-modal').style.display = 'block';
        }

        function closeSummaryMessagesModal() {
            document.getElementById('summary-messages-modal').style.display = 'none';
        }

        function addExtra(id) {
            if (!extraSelection.includes(id)) {
                extraSelection.push(id);
                selectionDirty = true;
                updateDirtyHint();
                renderSummaries();
            }
        }

        function removeExtra(id) {
            extraSelection = extraSelection.filter(x => x !== id);
            selectionDirty = true;
            updateDirtyHint();
            renderSummaries();
        }

        function updateDirtyHint() {
            const hint = document.getElementById('selection-dirty-hint');
            if (hint) hint.style.display = selectionDirty ? 'inline' : 'none';
        }

        function toggleEditSummary(id) {
            const viewEl = document.getElementById(`summary-view-${id}`);
            const editEl = document.getElementById(`summary-edit-${id}`);
            if (editEl.style.display === 'none') {
                viewEl.style.display = 'none';
                editEl.style.display = 'block';
                isEditing = true;
            } else {
                viewEl.style.display = 'block';
                editEl.style.display = 'none';
                isEditing = false;
            }
        }

        async function saveSummaryEdit(id) {
            const textarea = document.getElementById(`summary-textarea-${id}`);
            const newText = textarea.value;
            await fetch(`/api/projects/${currentProject}/summaries/${id}`, {
                method: 'PUT',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({summary_text: newText})
            });
            const s = summariesData.find(x => x.id === id);
            if (s) s.summary_text = newText;
            isEditing = false;
            toggleEditSummary(id);
            renderSummaries();
        }

        async function regenerateSummary(id) {
            const btn = event.target;
            btn.disabled = true;
            btn.textContent = '...';
            const res = await fetch(`/api/projects/${currentProject}/summaries/${id}/regenerate`, {method: 'POST'});
            const data = await res.json();
            btn.disabled = false;
            btn.textContent = 'Regen';
            if (data.error) {
                console.error('Regenerate error:', data.error);
                return;
            }
            const s = summariesData.find(x => x.id === id);
            if (s) s.summary_text = data.summary_text;
            renderSummaries();
        }

        async function triggerSummary() {
            if (!currentProject) return;
            const btn = event.target;
            btn.disabled = true;
            btn.textContent = 'Summarizing...';
            try {
                const res = await fetch(`/api/projects/${currentProject}/summaries/trigger`, {method: 'POST'});
                const data = await res.json();
                if (data.created) {
                    loadSummaries();
                } else {
                    alert(data.message || 'No summary created');
                }
            } catch (e) {
                console.error('Trigger summary error:', e);
            }
            btn.disabled = false;
            btn.textContent = 'Summarize Now';
        }

        async function regenerateAllSummaries() {
            if (!currentProject) return;
            const btn = event.target;
            btn.disabled = true;
            btn.textContent = 'Regenerating...';
            const res = await fetch(`/api/projects/${currentProject}/summaries/regenerate-all`, {method: 'POST'});
            const data = await res.json();
            btn.disabled = false;
            btn.textContent = 'Regenerate All';
            if (data.error) {
                console.error('Regenerate all error:', data.error);
                return;
            }
            loadSummaries();
        }

        async function saveSummarySelection() {
            if (!currentProject) return;
            await fetch(`/api/projects/${currentProject}/summaries/selection`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({selected_ids: extraSelection})
            });
            selectionDirty = false;
            updateDirtyHint();
        }

        let draggedId = null;
        function onDragStart(e) {
            draggedId = parseInt(e.target.dataset.id);
            e.dataTransfer.effectAllowed = 'move';
            isEditing = true;
        }
        function onDragOver(e) {
            e.preventDefault();
            e.dataTransfer.dropEffect = 'move';
        }
        function onDrop(e) {
            e.preventDefault();
            isEditing = false;
            const targetEl = e.target.closest('.card[data-id]');
            if (!targetEl) return;
            const targetId = parseInt(targetEl.dataset.id);
            if (draggedId === targetId) return;
            // 只在 extra 列表内重新排序
            if (!extraSelection.includes(draggedId) || !extraSelection.includes(targetId)) return;
            const draggedIdx = extraSelection.indexOf(draggedId);
            const targetIdx = extraSelection.indexOf(targetId);
            extraSelection.splice(draggedIdx, 1);
            extraSelection.splice(targetIdx, 0, draggedId);
            selectionDirty = true;
            updateDirtyHint();
            renderSummaries();
        }
        function onDragEnd(e) {
            isEditing = false;
        }

        async function loadContext() {
            if (!currentProject) return;
            const res = await fetch(`/api/projects/${currentProject}/context`);
            const data = await res.json();
            let html = '<div class="context-preview">';
            if (data.summaries) {
                html += `<div class="context-section"><div class="context-label">Historical Summaries:</div><div class="summary-text">${escapeHtml(data.summaries)}</div></div>`;
            }
            // 显示累积知识（与 sessionStart.py 一致：全部 6 类）
            if (data.knowledge) {
                const k = data.knowledge;
                const catNames = (window.i18n && window.i18n.category_names) || {};
                let knowledgeHtml = '';
                const categories = ['user_preferences', 'project_decisions', 'key_facts', 'pending_tasks', 'learned_patterns', 'important_context'];
                for (const cat of categories) {
                    if (k[cat] && k[cat].length > 0) {
                        const label = catNames[cat] || cat;
                        knowledgeHtml += `<div style="margin: 5px 0;"><strong>${escapeHtml(label)}:</strong> ${escapeHtml(k[cat].join(', '))}</div>`;
                    }
                }
                if (knowledgeHtml) {
                    html += `<div class="context-section"><div class="context-label">Accumulated Knowledge:</div><div class="summary-text">${knowledgeHtml}</div></div>`;
                }
            }
            if (data.messages && data.messages.length > 0) {
                html += `<div class="context-section"><div class="context-label">Recent Messages (${data.messages.length}):</div>`;
                data.messages.forEach(m => {
                    html += `<div class="message ${m.role}"><div class="message-role">${m.role}</div><div class="message-content">${escapeHtml(m.content)}</div></div>`;
                });
                html += '</div>';
            }
            if (!data.summaries && (!data.messages || data.messages.length === 0) && !data.knowledge) {
                html += '<p style="color: #888;">No context available for this project yet.</p>';
            }
            html += '</div>';
            const el = document.getElementById('context-preview');
            el.innerHTML = html;
            el.scrollTop = el.scrollHeight;
        }

        function onSearchMethodChange() {
            const method = document.getElementById('search-method').value;
            document.getElementById('fuzzy-options').style.display = method === 'fuzzy' ? 'block' : 'none';
        }

        async function doUnifiedSearch() {
            const query = document.getElementById('search-query').value;
            if (!query) return;

            const scope = document.getElementById('search-scope').value;
            const method = document.getElementById('search-method').value;
            const threshold = document.getElementById('fuzzy-threshold').value || 60;

            if (scope === 'current' && !currentProject) {
                document.getElementById('search-results').innerHTML = '<div class="card" style="color: #888;">Please select a project first, or search all projects.</div>';
                return;
            }

            document.getElementById('search-status').textContent = 'Searching...';
            document.getElementById('search-results').innerHTML = '';

            let url = `/api/search?query=${encodeURIComponent(query)}&method=${method}&scope=${scope}&limit=30`;
            if (scope === 'current') {
                url += `&project=${encodeURIComponent(currentProject)}`;
            }
            if (method === 'fuzzy') {
                url += `&threshold=${threshold}`;
            }

            try {
                const res = await fetch(url);
                const data = await res.json();

                if (data.error) {
                    document.getElementById('search-status').textContent = `Error: ${data.error}`;
                    return;
                }

                const methodLabels = {vector: '🧠 Semantic', bm25: '🔑 Keyword', fuzzy: '〰️ Fuzzy', combined: '🔗 Combined'};
                document.getElementById('search-status').textContent = `Found ${data.total || 0} results using ${methodLabels[data.method] || data.method}`;

                document.getElementById('search-results').innerHTML = data.results.map(r => {
                    const isUser = r.role === 'user';
                    const bgColor = isUser ? '#1a3a5c' : '#2d1f3d';
                    const borderColor = isUser ? '#00d9ff' : '#e94560';
                    const roleLabel = isUser ? 'user' : formatModelName(r.model);
                    const methodBadge = r.method ? `<span style="background: ${r.method === 'vector' ? '#4a90d9' : '#d94a90'}; color: #fff; padding: 1px 6px; border-radius: 3px; font-size: 0.7em; margin-left: 5px;">${r.method}</span>` : '';
                    const scoreBadge = r.score > 0 ? `<span style="color: #888; font-size: 0.8em; margin-left: 8px;">score: ${r.score}</span>` : '';
                    const projectBadge = scope === 'all' ? `<span class="badge" style="margin-left: 5px;">${r.project}</span>` : '';
                    return `
                    <div class="card" style="margin-bottom: 10px; background: ${bgColor}; border-left: 3px solid ${borderColor};">
                        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                            <div>
                                <span style="color: ${borderColor}; font-weight: bold;">${roleLabel}</span>
                                ${projectBadge}${methodBadge}${scoreBadge}
                            </div>
                            <span style="color: #666; font-size: 0.75em;">${r.timestamp}</span>
                        </div>
                        <div style="white-space: pre-wrap; word-break: break-word; line-height: 1.5; color: #eee;">${escapeHtml(r.content)}</div>
                    </div>`;
                }).join('') || '<div class="card" style="color: #888;">No results found</div>';
            } catch (e) {
                document.getElementById('search-status').textContent = `Error: ${e.message}`;
            }
        }

        // Legacy search for backward compatibility
        async function doSearch() {
            doUnifiedSearch();
        }

        let logEventSource = null;
        let logLineCount = 0;
        const MAX_LOG_LINES = 1000;

        function formatLogLine(log) {
            const line = log.line || log;
            const source = log.source || 'app';
            let cls = 'log-line';
            let levelMatch = line.match(/\| (ERROR|WARNING|INFO|DEBUG|CRITICAL)\s*\|/i);
            if (levelMatch) {
                cls += ' ' + levelMatch[1].toLowerCase();
            } else if (line.includes('ERROR') || line.includes('error')) {
                cls += ' error';
            } else if (line.includes('WARNING') || line.includes('warning')) {
                cls += ' warning';
            } else if (line.includes('INFO')) {
                cls += ' info';
            } else if (line.includes('DEBUG')) {
                cls += ' debug';
            }

            const sourceTag = `<span class="log-source">[${source}]</span> `;
            return `<div class="${cls}" data-level="${cls.split(' ')[1] || 'other'}" data-source="${source}">${sourceTag}${escapeHtml(line)}</div>`;
        }

        async function loadLogs() {
            const source = document.getElementById('log-source').value;
            const res = await fetch(`/api/logs?lines=300&source=${source}`);
            const data = await res.json();
            const el = document.getElementById('log-list');
            el.innerHTML = data.logs.map(log => formatLogLine(log)).join('');
            logLineCount = data.logs.length;
            filterLogLevel();
            if (document.getElementById('log-autoscroll').checked) {
                el.scrollTop = el.scrollHeight;
            }
            updateLogStatus(`Loaded ${data.logs.length} lines`);
        }

        function appendLogLine(log) {
            const el = document.getElementById('log-list');
            el.insertAdjacentHTML('beforeend', formatLogLine(log));
            logLineCount++;

            // 限制最大行数
            if (logLineCount > MAX_LOG_LINES) {
                const firstChild = el.firstElementChild;
                if (firstChild) {
                    firstChild.remove();
                    logLineCount--;
                }
            }

            filterLogLevel();
            if (document.getElementById('log-autoscroll').checked) {
                el.scrollTop = el.scrollHeight;
            }
        }

        function filterLogLevel() {
            const level = document.getElementById('log-level').value;
            const lines = document.querySelectorAll('#log-list .log-line');
            lines.forEach(line => {
                if (level === 'all') {
                    line.classList.remove('hidden');
                } else {
                    const lineLevel = line.dataset.level;
                    line.classList.toggle('hidden', lineLevel !== level && lineLevel !== 'other');
                }
            });
        }

        function clearLogDisplay() {
            document.getElementById('log-list').innerHTML = '';
            logLineCount = 0;
            updateLogStatus('Cleared');
        }

        function updateLogStatus(msg) {
            const el = document.getElementById('log-status');
            el.textContent = `${new Date().toLocaleTimeString()} - ${msg}`;
        }

        function toggleRealtimeLogs() {
            const enabled = document.getElementById('log-realtime').checked;
            if (enabled) {
                startRealtimeLogs();
            } else {
                stopRealtimeLogs();
            }
        }

        function startRealtimeLogs() {
            if (logEventSource) {
                logEventSource.close();
            }
            updateLogStatus('Connecting to realtime stream...');
            logEventSource = new EventSource('/api/logs/stream');

            logEventSource.onmessage = (event) => {
                const data = JSON.parse(event.data);
                if (data.type === 'connected') {
                    updateLogStatus('Realtime: Connected');
                } else if (data.type === 'log') {
                    appendLogLine(data.data);
                }
            };

            logEventSource.onerror = () => {
                updateLogStatus('Realtime: Disconnected (retrying...)');
            };
        }

        function stopRealtimeLogs() {
            if (logEventSource) {
                logEventSource.close();
                logEventSource = null;
                updateLogStatus('Realtime: Stopped');
            }
        }

        let configMeta = {};
        let configDefaults = {};

        function openConfigModal() {
            document.getElementById('config-modal').style.display = 'block';
            loadConfig();
        }

        function closeConfigModal() {
            document.getElementById('config-modal').style.display = 'none';
        }

        async function loadConfig() {
            const res = await fetch('/api/config');
            const data = await res.json();
            configMeta = data.meta || {};
            configDefaults = data.defaults || {};
            const config = data.config || {};
            const defaultPrompts = data.default_prompts || {};
            // 存储 i18n 数据供全局使用
            window.i18n = data.i18n || {
                category_names: {},
                role_labels: {},
                ui_text: {}
            };

            const groups = {
                'Memory': {
                    icon: '🧠',
                    keys: ['short_term_window_size', 'max_context_tokens', 'summary_trigger_threshold']
                },
                'LLM (Ollama)': {
                    icon: '🤖',
                    keys: ['llm_provider', 'ollama_model', 'ollama_base_url', 'ollama_timeout', 'ollama_keep_alive', 'anthropic_model']
                },
                'Embedding': {
                    icon: '🔢',
                    keys: ['embedding_model', 'embedding_base_url', 'enable_vector_search']
                },
                'Search': {
                    icon: '🔍',
                    keys: ['search_result_preview_length']
                },
                'Knowledge': {
                    icon: '📚',
                    keys: ['enable_knowledge_extraction', 'knowledge_max_items_per_category']
                },
                'Content': {
                    icon: '📄',
                    keys: ['content_include_thinking', 'content_include_tool', 'content_include_text', 'content_max_chars_thinking', 'content_max_chars_tool', 'content_max_chars_text']
                },
                'Inject': {
                    icon: '💉',
                    keys: ['inject_summary_count', 'inject_recent_count', 'inject_knowledge_count', 'inject_task_count']
                },
                'Summary': {
                    icon: '📝',
                    keys: ['summary_max_chars_total']
                },
                'Stats': {
                    icon: '📊',
                    keys: ['input_token_price', 'output_token_price']
                },
                'Dashboard': {
                    icon: '🖥️',
                    keys: ['dashboard_refresh_interval']
                },
                'Prompts': {
                    icon: '✏️',
                    keys: ['prompt_language', 'summary_prompt_template', 'knowledge_extraction_prompt', 'decision_extraction_prompt']
                }
            };

            function renderConfigItem(key) {
                const meta = configMeta[key] || {label: key, description: '', type: 'text'};
                const value = config[key] || configDefaults[key] || '';
                const tooltip = meta.tooltip || '';
                const tooltipHtml = tooltip ? `<span class="tooltip-icon" data-tooltip="${escapeHtml(tooltip)}">?</span>` : '';
                const defaultPrompt = defaultPrompts[key] || '';

                let itemHtml = `<div class="config-item" style="margin-bottom: 16px; padding: 12px; background: #0a1929; border-radius: 8px;">`;
                itemHtml += `<label style="display: flex; align-items: center; gap: 8px; color: #00d9ff; font-weight: bold; margin-bottom: 5px;">
                    <span>${meta.label || key}</span>${tooltipHtml}
                </label>`;
                itemHtml += `<div style="color: #888; font-size: 0.85em; margin-bottom: 8px;">${meta.description || ''}</div>`;
                if (meta.type === 'select' && meta.options) {
                    itemHtml += `<select id="config-${key}" style="width: 100%; max-width: 400px; padding: 10px; background: #1a3a5c; color: #eee; border: 1px solid #333; border-radius: 5px;">`;
                    for (const opt of meta.options) {
                        const optValue = typeof opt === 'object' ? opt.value : opt;
                        const optLabel = typeof opt === 'object' ? opt.label : opt;
                        itemHtml += `<option value="${optValue}" ${value === optValue ? 'selected' : ''}>${optLabel}</option>`;
                    }
                    itemHtml += `</select>`;
                } else if (meta.type === 'number') {
                    itemHtml += `<input type="number" id="config-${key}" value="${value}" min="${meta.min || 0}" max="${meta.max || 99999}" style="width: 100%; max-width: 400px; padding: 10px; background: #1a3a5c; color: #eee; border: 1px solid #333; border-radius: 5px;">`;
                } else if (meta.type === 'textarea') {
                    const placeholder = defaultPrompt ? escapeHtml(defaultPrompt) : '';
                    const displayValue = value || '';
                    itemHtml += `<textarea id="config-${key}" placeholder="${placeholder}" style="width: 100%; min-height: 150px; padding: 10px; background: #1a3a5c; color: #eee; border: 1px solid #333; border-radius: 5px; font-family: monospace; font-size: 12px;">${escapeHtml(displayValue)}</textarea>`;
                    if (defaultPrompt) {
                        itemHtml += `<div style="margin-top: 8px;"><button type="button" onclick="document.getElementById('config-${key}').value = defaultPrompts['${key}']" style="padding: 4px 10px; background: #2a4a6a; color: #ccc; border: none; border-radius: 4px; cursor: pointer; font-size: 12px;">Show Default</button></div>`;
                    }
                } else {
                    itemHtml += `<input type="text" id="config-${key}" value="${value}" style="width: 100%; max-width: 400px; padding: 10px; background: #1a3a5c; color: #eee; border: 1px solid #333; border-radius: 5px;">`;
                }
                itemHtml += `</div>`;
                return itemHtml;
            }

            let html = '<div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(400px, 1fr)); gap: 20px;">';
            for (const [groupName, groupData] of Object.entries(groups)) {
                html += `<div class="config-group" style="background: #0f2847; border-radius: 10px; padding: 16px; border: 1px solid #1a3a5c;">`;
                html += `<div style="display: flex; align-items: center; gap: 10px; margin-bottom: 16px; padding-bottom: 12px; border-bottom: 1px solid #1a3a5c;">
                    <span style="font-size: 1.5em;">${groupData.icon}</span>
                    <h3 style="margin: 0; color: #00d9ff;">${groupName}</h3>
                </div>`;
                for (const key of groupData.keys) {
                    html += renderConfigItem(key);
                }
                html += `</div>`;
            }
            html += '</div>';
            // 存储 defaultPrompts 供按钮使用
            window.defaultPrompts = defaultPrompts;
            document.getElementById('config-form').innerHTML = html;
        }

        async function saveAllConfig() {
            const configKeys = [
                'short_term_window_size', 'max_context_tokens', 'summary_trigger_threshold',
                'llm_provider', 'ollama_model', 'ollama_base_url', 'ollama_timeout', 'ollama_keep_alive',
                'anthropic_model', 'embedding_model', 'embedding_base_url', 'enable_vector_search', 'enable_knowledge_extraction',
                'input_token_price', 'output_token_price',
                'inject_summary_count', 'inject_recent_count', 'inject_knowledge_count', 'inject_task_count',
                'summary_max_chars_total',
                'content_include_thinking', 'content_include_tool', 'content_include_text',
                'content_max_chars_thinking', 'content_max_chars_tool', 'content_max_chars_text',
                'knowledge_max_items_per_category',
                'search_result_preview_length', 'dashboard_refresh_interval',
                'prompt_language', 'summary_prompt_template', 'knowledge_extraction_prompt', 'decision_extraction_prompt'
            ];
            for (const key of configKeys) {
                const el = document.getElementById(`config-${key}`);
                if (el) {
                    await fetch('/api/config', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({key, value: el.value})});
                }
            }
            alert('Settings saved!');
            closeConfigModal();
        }

        async function resetConfig() {
            if (!confirm('Reset all settings to defaults?')) return;
            for (const [key, value] of Object.entries(configDefaults)) {
                await fetch('/api/config', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({key, value})});
            }
            loadConfig();
        }

        function escapeHtml(text) {
            if (!text) return '';
            return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
        }

        // ========== Common UI Utilities ==========
        function formatDateTime(timestamp) {
            const date = new Date(timestamp);
            return date.toLocaleDateString() + ' ' + date.toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'});
        }

        function renderHistoryItem(opts) {
            // opts: { id, timestamp, isSelected, borderColor, onClick, headerExtra, lines: [{text, color, size, ellipsis}] }
            const bg = opts.isSelected ? '#2a4a6a' : '#1a1a2e';
            const border = opts.isSelected ? '#00d9ff' : (opts.borderColor || '#333');
            const linesHtml = (opts.lines || []).map(l =>
                `<div style="font-size: ${l.size || '0.8em'}; color: ${l.color || '#888'}; margin-top: 3px; ${l.ellipsis ? 'overflow: hidden; text-overflow: ellipsis; white-space: nowrap;' : ''}">${escapeHtml(l.text)}</div>`
            ).join('');
            return `<div onclick="${opts.onClick}" style="padding: 8px; margin-bottom: 5px; background: ${bg}; border-radius: 6px; cursor: pointer; border-left: 3px solid ${border};">
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <span style="font-size: 0.8em; color: #00d9ff;">#${opts.id}</span>
                    ${opts.headerExtra || ''}
                </div>
                <div style="font-size: 0.75em; color: #888;">${formatDateTime(opts.timestamp)}</div>
                ${linesHtml}
            </div>`;
        }

        function toggleSummaryDebug() {
            const panel = document.getElementById('summary-debug-panel');
            if (panel.style.display === 'none') {
                panel.style.display = 'block';
                loadSummaryDebug();
            } else {
                panel.style.display = 'none';
            }
        }

        async function loadSummaryDebug() {
            if (!currentProject) {
                alert('Please select a project first');
                return;
            }
            const res = await fetch(`/api/projects/${currentProject}/summary-debug`);
            const data = await res.json();

            // 显示更详细的状态信息
            const statusColor = data.pending_count > 0 ? '#ffcc00' : '#4caf50';
            document.getElementById('debug-info').innerHTML = `
                <div style="display: flex; flex-wrap: wrap; gap: 15px; align-items: center;">
                    <div><strong>Latest Msg:</strong> <span style="color: #00d9ff;">#${data.latest_message_id}</span></div>
                    <div><strong>Last Summary:</strong> <span style="color: #4caf50;">→ #${data.last_summary_end_id}</span></div>
                    <div><strong>Pending:</strong> <span style="color: ${statusColor}; font-weight: bold;">${data.pending_count} messages</span></div>
                    <div><strong>Unsummarized:</strong> ${data.message_count}</div>
                    <div><strong>Custom Template:</strong> ${data.using_custom_template ? 'Yes' : 'No'}</div>
                </div>
            `;
            document.getElementById('debug-msg-count').textContent = data.message_count;
            document.getElementById('debug-messages').innerHTML = data.messages.map(m => `
                <div style="margin: 3px 0; padding: 6px; border-radius: 4px; background: ${m.role === 'user' ? '#1a3a4a' : '#2a1a4a'}; font-size: 0.85em;">
                    <span style="color: ${m.role === 'user' ? '#00d9ff' : '#ff6b9d'}; font-weight: bold;">${m.role}</span>
                    <span style="color: #888; margin-left: 5px;">#${m.id}</span>
                    <span style="color: #666; margin-left: 5px; font-size: 0.8em;">[${m.session_id ? m.session_id.substring(0, 8) : 'unknown'}...]</span>
                    <div style="color: #ccc; margin-top: 3px;">${escapeHtml(m.content.substring(0, 150))}${m.content.length > 150 ? '...' : ''}</div>
                </div>
            `).join('') || '<p style="color: #888;">No unsummarized messages (all caught up!)</p>';
            document.getElementById('debug-prompt').textContent = data.full_prompt || 'No prompt generated';
        }

        async function loadVectorStats() {
            if (!currentProject) {
                document.getElementById('vector-stats').textContent = 'Select a project first';
                return;
            }
            try {
                const res = await fetch(`/api/projects/${currentProject}/vectors/stats`);
                const data = await res.json();
                if (data.error) {
                    document.getElementById('vector-stats').innerHTML = `<span style="color: #e94560;">Error: ${data.error}</span>`;
                } else {
                    document.getElementById('vector-stats').innerHTML = `Vectors: <strong>${data.total_vectors}</strong> | Mapped: <strong>${data.mapped_messages}</strong> | Dim: ${data.dimension}`;
                }
            } catch (e) {
                document.getElementById('vector-stats').innerHTML = `<span style="color: #e94560;">Failed to load</span>`;
            }
        }

        async function rebuildVectors() {
            if (!currentProject) {
                alert('Please select a project first');
                return;
            }
            if (!confirm('This will clear and rebuild all vector embeddings.\nThis may take a while for large projects.\n\nContinue?')) {
                return;
            }
            const btn = event.target;
            btn.disabled = true;
            btn.textContent = 'Rebuilding...';
            document.getElementById('vector-stats').innerHTML = '<span style="color: #d9a04a;">Rebuilding...</span>';

            try {
                const res = await fetch(`/api/projects/${currentProject}/vectors/rebuild`, {method: 'POST'});
                const data = await res.json();
                btn.disabled = false;
                btn.textContent = 'Rebuild Vectors';

                if (data.error) {
                    alert('Error: ' + data.error);
                    loadVectorStats();
                } else {
                    document.getElementById('vector-stats').innerHTML = `<span style="color: #4ad9a0;">✓ Rebuilt ${data.rebuilt}/${data.total_messages} vectors</span>`;
                    setTimeout(loadVectorStats, 2000);
                }
            } catch (e) {
                btn.disabled = false;
                btn.textContent = 'Rebuild Vectors';
                alert('Failed to rebuild: ' + e.message);
                loadVectorStats();
            }
        }

        async function refreshAll() {
            // 某些页面不自动刷新
            if (currentPanel === 'knowledge' || currentPanel === 'search') {
                return;
            }
            // 编辑中不刷新，避免打断用户操作
            if (isEditing || selectionDirty) {
                return;
            }
            await loadProjects();
            loadProjectData();
        }


        let knowledgeHistory = [];
        let selectedKnowledgeHistoryId = null;

        async function loadKnowledge() {
            if (!currentProject) {
                document.getElementById('knowledge-status').textContent = 'Select a project first';
                return;
            }
            document.getElementById('knowledge-status').textContent = 'Loading...';
            const res = await fetch(`/api/projects/${currentProject}/knowledge`);
            let data = await res.json();
            document.getElementById('knowledge-status').textContent = '';

            selectedKnowledgeHistoryId = null;
            document.getElementById('knowledge-viewing-label').textContent = 'Current';
            renderKnowledgeContent(data.knowledge || {}, data.max_per_category || 10);
            loadKnowledgeHistory();
        }

        function renderKnowledgeContent(k, maxPerCategory) {
            const categories = ['user-preferences', 'project-decisions', 'key-facts', 'pending-tasks', 'learned-patterns', 'important-context'];
            const keys = ['user_preferences', 'project_decisions', 'key_facts', 'pending_tasks', 'learned_patterns', 'important_context'];
            for (let i = 0; i < categories.length; i++) {
                const items = k[keys[i]] || [];
                const badge = ` (${items.length}/${maxPerCategory})`;
                document.getElementById(`k-${categories[i]}`).innerHTML = items.length > 0
                    ? `<div style="color: #888; margin-bottom: 5px; font-size: 0.85em;">${badge}</div>` + items.map(item => `<li style="margin: 5px 0; color: #ccc;">${escapeHtml(item)}</li>`).join('')
                    : '<li style="color: #666;">No items</li>';
            }
        }

        async function loadKnowledgeHistory() {
            if (!currentProject) return;
            try {
                const res = await fetch(`/api/projects/${currentProject}/knowledge/history`);
                const data = await res.json();
                knowledgeHistory = data.history || [];
                renderKnowledgeHistory();
            } catch (e) {
                console.error('Failed to load knowledge history:', e);
            }
        }

        function renderKnowledgeHistory() {
            const container = document.getElementById('knowledge-history-list');
            if (knowledgeHistory.length === 0) {
                container.innerHTML = '<div style="color: #666; font-size: 0.85em;">No history yet</div>';
                return;
            }
            container.innerHTML = knowledgeHistory.map(h => {
                const totalItems = Object.values(h.content || {}).reduce((sum, arr) => sum + (arr?.length || 0), 0);
                return renderHistoryItem({
                    id: h.id,
                    timestamp: h.created_at,
                    isSelected: selectedKnowledgeHistoryId === h.id,
                    onClick: `viewKnowledgeHistory(${h.id})`,
                    lines: [{text: `${totalItems} items`, color: '#666', size: '0.75em'}]
                });
            }).join('');
        }

        let editingKnowledgeHistoryId = null;

        function viewKnowledgeHistory(id) {
            const h = knowledgeHistory.find(x => x.id === id);
            if (!h) return;
            selectedKnowledgeHistoryId = id;
            editingKnowledgeHistoryId = null;
            const date = new Date(h.created_at);
            document.getElementById('knowledge-viewing-label').innerHTML = `Viewing #${id} (${date.toLocaleDateString()}) <button onclick="editKnowledgeHistory(${id})" style="margin-left: 10px; padding: 2px 8px; font-size: 0.8em;">Edit</button>`;
            renderKnowledgeContent(h.content || {}, 999);
            renderKnowledgeHistory();
        }

        function editKnowledgeHistory(id) {
            const h = knowledgeHistory.find(x => x.id === id);
            if (!h) return;
            editingKnowledgeHistoryId = id;
            const date = new Date(h.created_at);
            document.getElementById('knowledge-viewing-label').innerHTML = `Editing #${id} (${date.toLocaleDateString()}) <button onclick="saveKnowledgeHistory(${id})" style="margin-left: 10px; padding: 2px 8px; font-size: 0.8em; background: #4ad9a0;">Save</button> <button onclick="viewKnowledgeHistory(${id})" style="padding: 2px 8px; font-size: 0.8em; background: #333;">Cancel</button>`;
            renderKnowledgeContentEditable(h.content || {});
        }

        function renderKnowledgeContentEditable(k) {
            const categories = ['user-preferences', 'project-decisions', 'key-facts', 'pending-tasks', 'learned-patterns', 'important-context'];
            const keys = ['user_preferences', 'project_decisions', 'key_facts', 'pending_tasks', 'learned_patterns', 'important_context'];
            for (let i = 0; i < categories.length; i++) {
                const items = k[keys[i]] || [];
                const textarea = `<textarea id="k-edit-${keys[i]}" style="width: 100%; min-height: 80px; background: #1a1a2e; color: #eee; border: 1px solid #333; border-radius: 5px; padding: 6px; font-size: 0.85em;">${items.join('\n')}</textarea>`;
                document.getElementById(`k-${categories[i]}`).innerHTML = textarea;
            }
        }

        async function saveKnowledgeHistory(id) {
            const keys = ['user_preferences', 'project_decisions', 'key_facts', 'pending_tasks', 'learned_patterns', 'important_context'];
            const content = {};
            for (const key of keys) {
                const textarea = document.getElementById(`k-edit-${key}`);
                if (textarea) {
                    content[key] = textarea.value.split('\n').map(s => s.trim()).filter(s => s);
                }
            }
            try {
                await fetch(`/api/projects/${currentProject}/knowledge/history/${id}`, {
                    method: 'PUT',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({content})
                });
                // Update local cache
                const h = knowledgeHistory.find(x => x.id === id);
                if (h) h.content = content;
                viewKnowledgeHistory(id);
                document.getElementById('knowledge-status').textContent = 'Saved';
                setTimeout(() => document.getElementById('knowledge-status').textContent = '', 2000);
            } catch (e) {
                document.getElementById('knowledge-status').textContent = 'Error: ' + e.message;
            }
        }

        async function extractKnowledge() {
            if (!currentProject) {
                alert('Please select a project first');
                return;
            }
            const btn = event.target;
            btn.disabled = true;
            btn.textContent = 'Extracting...';
            document.getElementById('knowledge-status').textContent = 'Sending to LLM...';
            const res = await fetch(`/api/projects/${currentProject}/knowledge/extract`, {method: 'POST'});
            const data = await res.json();
            btn.disabled = false;
            btn.textContent = 'Extract New';
            document.getElementById('knowledge-status').textContent = '';
            if (data.error) {
                alert('Error: ' + data.error);
                return;
            }
            const newItems = Object.values(data.extracted || {}).flat().length;
            document.getElementById('knowledge-status').textContent = `Extracted ${newItems} new items`;
            setTimeout(() => { document.getElementById('knowledge-status').textContent = ''; }, 3000);
            loadKnowledge();
        }

        async function loadKnowledgeDebug() {
            if (!currentProject) {
                alert('Please select a project first');
                return;
            }
            const res = await fetch(`/api/projects/${currentProject}/knowledge-debug`);
            const data = await res.json();

            const cfg = data.content_config || {};
            document.getElementById('knowledge-debug-info').innerHTML = `
                <div>Source: <strong>${data.message_source}</strong> | Messages: <strong>${data.message_count}</strong></div>
                <div style="font-size: 0.85em; color: #888; margin-top: 4px;">Content: thinking=${cfg.include_thinking ? 'on' : 'off'} (${cfg.max_chars_thinking}), tool=${cfg.include_tool ? 'on' : 'off'} (${cfg.max_chars_tool}), text=${cfg.include_text ? 'on' : 'off'} (${cfg.max_chars_text})</div>
            `;

            document.getElementById('knowledge-debug-messages').innerHTML = data.messages.map(m => `
                <div style="padding: 5px; margin: 3px 0; background: ${m.role === 'user' ? '#1a3a5c' : '#2d1f3d'}; border-radius: 4px; font-size: 0.85em;">
                    <span style="color: ${m.role === 'user' ? '#00d9ff' : '#e94560'}; font-weight: bold;">${m.role}</span>
                    <span style="color: #888; margin-left: 8px;">${escapeHtml(m.content.substring(0, 100))}...</span>
                </div>
            `).join('') || '<div style="color: #888;">No messages</div>';

            const noKnowledgeText = (window.i18n && window.i18n.ui_text && window.i18n.ui_text.no_existing_knowledge) || '(No existing knowledge)';
            document.getElementById('knowledge-debug-existing').textContent = data.existing_knowledge || noKnowledgeText;
            document.getElementById('knowledge-debug-prompt').textContent = data.full_prompt || 'No prompt';
            document.getElementById('knowledge-debug-panel').style.display = 'block';
        }

        // ========== Decisions Functions ==========
        let currentDecisionFilter = 'pending';
        let searchDecisionsTimeout = null;

        let allDecisions = [];
        let selectedDecisionId = null;

        async function loadDecisions() {
            if (!currentProject) return;
            document.getElementById('decisions-status').textContent = 'Loading...';

            try {
                const res = await fetch(`/api/projects/${currentProject}/decisions?status=${currentDecisionFilter}`);
                const data = await res.json();

                allDecisions = data.decisions || [];

                // Update pending badge
                const badge = document.getElementById('pending-decisions-badge');
                if (data.pending_count > 0) {
                    badge.textContent = data.pending_count;
                    badge.style.display = 'inline';
                } else {
                    badge.style.display = 'none';
                }

                document.getElementById('pending-count-label').textContent = `(${data.pending_count})`;
                document.getElementById('decision-count-label').textContent = `(${allDecisions.length})`;

                // Render history list (sidebar)
                renderDecisionHistoryList();

                // Separate pending and confirmed
                const pending = allDecisions.filter(d => d.status === 'pending');
                const confirmed = allDecisions.filter(d => d.status === 'confirmed');

                // Render pending decisions
                document.getElementById('pending-decisions-list').innerHTML = pending.length > 0
                    ? pending.map(d => renderPendingDecision(d)).join('')
                    : '<div style="color: #888; padding: 10px;">No pending decisions</div>';

                // Render confirmed decisions
                document.getElementById('confirmed-decisions-list').innerHTML = confirmed.length > 0
                    ? confirmed.map(d => renderConfirmedDecision(d)).join('')
                    : '<div style="color: #888; padding: 10px;">No confirmed decisions</div>';

                document.getElementById('decisions-status').textContent = '';
            } catch (e) {
                document.getElementById('decisions-status').textContent = 'Error: ' + e.message;
            }
        }

        function renderDecisionHistoryList() {
            const container = document.getElementById('decision-history-list');
            if (allDecisions.length === 0) {
                container.innerHTML = '<div style="color: #666; font-size: 0.85em;">No decisions yet</div>';
                return;
            }
            // Sort by timestamp desc (newest first)
            const sorted = [...allDecisions].sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp));
            container.innerHTML = sorted.map(d => {
                const statusColor = d.status === 'pending' ? '#e94560' : d.status === 'confirmed' ? '#4ad9a0' : '#888';
                return renderHistoryItem({
                    id: d.id,
                    timestamp: d.timestamp,
                    isSelected: selectedDecisionId === d.id,
                    borderColor: statusColor,
                    onClick: `viewDecisionDetail(${d.id})`,
                    headerExtra: `<span style="font-size: 0.7em; color: ${statusColor}; text-transform: uppercase; float: right;">${d.status}</span>`,
                    lines: [{text: d.problem.substring(0, 50) + (d.problem.length > 50 ? '...' : ''), color: '#ccc', size: '0.8em', ellipsis: true}]
                });
            }).join('');
        }

        let editingDecisionId = null;

        function viewDecisionDetail(id) {
            const d = allDecisions.find(x => x.id === id);
            if (!d) return;
            selectedDecisionId = id;
            editingDecisionId = null;
            renderDecisionHistoryList();

            let files = [];
            try { files = typeof d.files === 'string' ? JSON.parse(d.files || '[]') : (d.files || []); } catch (e) {}
            const filesHtml = files.length > 0 ? `<div style="margin-top: 10px;"><strong style="color: #888;">Files:</strong> ${files.map(f => `<code style="background: #333; padding: 2px 6px; border-radius: 3px; margin-left: 5px;">${escapeHtml(f)}</code>`).join('')}</div>` : '';

            document.getElementById('decision-detail-title').innerHTML = `Decision #${d.id} (${d.status}) <button onclick="editDecisionDetail(${d.id})" style="margin-left: 10px; padding: 2px 8px; font-size: 0.8em;">Edit</button>`;
            document.getElementById('decision-detail-content').innerHTML = `
                <div style="margin-bottom: 12px;">
                    <strong style="color: #00d9ff;">Problem:</strong>
                    <div style="margin-top: 5px; color: #eee; white-space: pre-wrap;">${escapeHtml(d.problem)}</div>
                </div>
                <div style="margin-bottom: 12px;">
                    <strong style="color: #4ad9a0;">Solution:</strong>
                    <div style="margin-top: 5px; color: #eee; white-space: pre-wrap;">${escapeHtml(d.solution)}</div>
                </div>
                ${d.reason ? `<div style="margin-bottom: 12px;"><strong style="color: #e94560;">Reason:</strong><div style="margin-top: 5px; color: #ccc;">${escapeHtml(d.reason)}</div></div>` : ''}
                ${d.note ? `<div style="margin-bottom: 12px;"><strong style="color: #888;">Note:</strong><div style="margin-top: 5px; color: #ccc;">${escapeHtml(d.note)}</div></div>` : ''}
                ${filesHtml}
                <div style="margin-top: 10px; color: #666; font-size: 0.85em;">
                    Session: ${d.session_id || 'N/A'} | ${new Date(d.timestamp).toLocaleString()}
                </div>
            `;
            document.getElementById('decision-detail-view').style.display = 'block';
        }

        function editDecisionDetail(id) {
            const d = allDecisions.find(x => x.id === id);
            if (!d) return;
            editingDecisionId = id;

            let reasonOptions = [];
            try {
                reasonOptions = typeof d.reason_options === 'string' ? JSON.parse(d.reason_options || '[]') : (d.reason_options || []);
            } catch (e) { reasonOptions = []; }
            const reasonRadios = reasonOptions.map((r, i) =>
                `<label style="display: block; margin: 5px 0; cursor: pointer;">
                    <input type="radio" name="detail-reason-${id}" value="${escapeHtml(r)}" ${d.reason === r ? 'checked' : ''}> ${escapeHtml(r)}
                </label>`
            ).join('');

            document.getElementById('decision-detail-title').innerHTML = `Editing Decision #${d.id} <button onclick="saveDecisionDetail(${d.id})" style="margin-left: 10px; padding: 2px 8px; font-size: 0.8em; background: #4ad9a0;">Save</button> <button onclick="viewDecisionDetail(${d.id})" style="padding: 2px 8px; font-size: 0.8em; background: #333;">Cancel</button>`;
            document.getElementById('decision-detail-content').innerHTML = `
                <div style="margin-bottom: 12px;">
                    <strong style="color: #00d9ff;">Problem:</strong>
                    <div style="margin-top: 5px; color: #eee;">${escapeHtml(d.problem)}</div>
                </div>
                <div style="margin-bottom: 12px;">
                    <strong style="color: #4ad9a0;">Solution:</strong>
                    <div style="margin-top: 5px; color: #eee;">${escapeHtml(d.solution)}</div>
                </div>
                <div style="margin-bottom: 12px;">
                    <strong style="color: #e94560;">Reason:</strong>
                    <div style="margin-top: 5px;">
                        ${reasonRadios}
                        <label style="display: block; margin: 5px 0; cursor: pointer;">
                            <input type="radio" name="detail-reason-${id}" value="__other__" ${reasonOptions.indexOf(d.reason) === -1 && d.reason ? 'checked' : ''}> Other:
                            <input type="text" id="detail-reason-other-${id}" value="${reasonOptions.indexOf(d.reason) === -1 ? escapeHtml(d.reason || '') : ''}" style="margin-left: 5px; padding: 4px; background: #1a1a2e; border: 1px solid #333; color: #eee; width: 200px;">
                        </label>
                    </div>
                </div>
                <div style="margin-bottom: 12px;">
                    <strong style="color: #888;">Note:</strong>
                    <textarea id="detail-note-${id}" style="width: 100%; min-height: 60px; margin-top: 5px; background: #1a1a2e; color: #eee; border: 1px solid #333; border-radius: 5px; padding: 6px;">${escapeHtml(d.note || '')}</textarea>
                </div>
            `;
        }

        async function saveDecisionDetail(id) {
            const selectedRadio = document.querySelector(`input[name="detail-reason-${id}"]:checked`);
            let reason = selectedRadio ? selectedRadio.value : '';
            if (reason === '__other__') {
                reason = document.getElementById(`detail-reason-other-${id}`).value.trim();
            }
            const note = document.getElementById(`detail-note-${id}`).value.trim();

            try {
                await fetch(`/api/projects/${currentProject}/decisions/${id}`, {
                    method: 'PUT',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({reason, note, status: 'confirmed'})
                });
                // Update local cache
                const d = allDecisions.find(x => x.id === id);
                if (d) {
                    d.reason = reason;
                    d.note = note;
                    d.status = 'confirmed';
                }
                viewDecisionDetail(id);
                document.getElementById('decisions-status').textContent = 'Saved';
                setTimeout(() => document.getElementById('decisions-status').textContent = '', 2000);
                loadDecisions(); // Refresh lists
            } catch (e) {
                document.getElementById('decisions-status').textContent = 'Error: ' + e.message;
            }
        }

        function closeDecisionDetail() {
            selectedDecisionId = null;
            editingDecisionId = null;
            document.getElementById('decision-detail-view').style.display = 'none';
            renderDecisionHistoryList();
        }

        async function extractDecisions() {
            if (!currentProject) {
                alert('Please select a project first');
                return;
            }
            const btn = event.target;
            btn.disabled = true;
            btn.textContent = 'Extracting...';
            document.getElementById('decisions-status').textContent = 'Sending to LLM...';

            try {
                const res = await fetch(`/api/projects/${currentProject}/decisions/extract`, {method: 'POST'});
                const data = await res.json();
                btn.disabled = false;
                btn.textContent = 'Extract Now';
                document.getElementById('decisions-status').textContent = '';

                if (data.error) {
                    document.getElementById('decisions-status').textContent = 'Error: ' + data.error;
                } else {
                    document.getElementById('decisions-status').textContent = data.message;
                    loadDecisions();
                }
            } catch (e) {
                btn.disabled = false;
                btn.textContent = 'Extract Now';
                document.getElementById('decisions-status').textContent = 'Error: ' + e.message;
            }
        }

        async function loadDecisionDebug() {
            if (!currentProject) {
                alert('Please select a project first');
                return;
            }
            document.getElementById('decisions-status').textContent = 'Loading prompt...';

            try {
                const res = await fetch(`/api/projects/${currentProject}/decisions/debug`);
                const data = await res.json();
                document.getElementById('decisions-status').textContent = '';

                const panel = document.getElementById('decision-debug-panel');
                const content = document.getElementById('decision-debug-content');
                content.textContent = `=== Messages: ${data.message_count}, Conversation: ${data.conversation_length} chars ===

${data.prompt}`;
                panel.style.display = 'block';
            } catch (e) {
                document.getElementById('decisions-status').textContent = 'Error: ' + e.message;
            }
        }

        function renderPendingDecision(d) {
            let options = [];
            let files = [];
            try { options = typeof d.reason_options === 'string' ? JSON.parse(d.reason_options || '[]') : (d.reason_options || []); } catch (e) {}
            try { files = typeof d.files === 'string' ? JSON.parse(d.files || '[]') : (d.files || []); } catch (e) {}
            return `
                <div class="card" style="margin-bottom: 15px; border-left: 3px solid #e94560;">
                    <div style="display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 10px;">
                        <span style="color: #888; font-size: 0.85em;">#${d.id} · ${new Date(d.timestamp).toLocaleString()}</span>
                        <div style="display: flex; gap: 5px;">
                            <button onclick="skipDecision(${d.id})" style="padding: 4px 10px; background: #333; font-size: 0.85em;">Skip</button>
                            <button onclick="deleteDecision(${d.id})" style="padding: 4px 10px; background: #e94560; font-size: 0.85em;">Delete</button>
                        </div>
                    </div>
                    <div style="margin-bottom: 8px;">
                        <strong style="color: #00d9ff;">Problem:</strong>
                        <div style="margin-top: 4px; color: #ccc;">${escapeHtml(d.problem)}</div>
                    </div>
                    <div style="margin-bottom: 8px;">
                        <strong style="color: #4ad9a0;">Solution:</strong>
                        <div style="margin-top: 4px; color: #ccc;">${escapeHtml(d.solution)}</div>
                    </div>
                    ${files.length > 0 ? `<div style="margin-bottom: 8px; color: #888; font-size: 0.85em;">Files: ${files.map(f => `<code>${escapeHtml(f)}</code>`).join(', ')}</div>` : ''}
                    <div style="margin-top: 10px; padding-top: 10px; border-top: 1px solid #333;">
                        <strong style="color: #d9a04a;">Select Reason:</strong>
                        <div id="reason-options-${d.id}" style="margin-top: 8px;">
                            ${options.map((opt, i) => `
                                <label style="display: block; margin-bottom: 6px; cursor: pointer;">
                                    <input type="radio" name="reason-${d.id}" value="${escapeHtml(opt)}" style="margin-right: 8px;">
                                    ${escapeHtml(opt)}
                                </label>
                            `).join('')}
                            <label style="display: block; margin-bottom: 6px; cursor: pointer;">
                                <input type="radio" name="reason-${d.id}" value="__other__" style="margin-right: 8px;">
                                Other: <input type="text" id="reason-other-${d.id}" placeholder="Enter custom reason..." style="padding: 4px 8px; width: 300px;">
                            </label>
                        </div>
                        <div style="margin-top: 10px;">
                            <label style="color: #888; font-size: 0.9em;">Note (optional):</label>
                            <textarea id="note-${d.id}" placeholder="Add any additional notes..." style="width: 100%; margin-top: 4px; padding: 8px; min-height: 60px;"></textarea>
                        </div>
                        <button onclick="confirmDecision(${d.id})" style="margin-top: 10px; padding: 8px 20px; background: #4ad9a0;">Confirm Decision</button>
                    </div>
                </div>
            `;
        }

        function renderConfirmedDecision(d) {
            return `
                <div class="card" style="margin-bottom: 10px; border-left: 3px solid #4ad9a0;">
                    <div style="display: flex; justify-content: space-between; align-items: flex-start;">
                        <div style="flex: 1;">
                            <div style="font-weight: bold; color: #00d9ff; margin-bottom: 4px;">${escapeHtml(d.problem)}</div>
                            <div style="color: #4ad9a0; margin-bottom: 4px;">→ ${escapeHtml(d.solution)}</div>
                            ${d.reason ? `<div style="color: #d9a04a; font-size: 0.9em;">Reason: ${escapeHtml(d.reason)}</div>` : ''}
                            ${d.note ? `<div style="color: #888; font-size: 0.85em; margin-top: 4px; font-style: italic;">Note: ${escapeHtml(d.note)}</div>` : ''}
                        </div>
                        <span style="color: #666; font-size: 0.8em; white-space: nowrap;">${new Date(d.timestamp).toLocaleDateString()}</span>
                    </div>
                </div>
            `;
        }

        async function confirmDecision(id) {
            const selectedRadio = document.querySelector(`input[name="reason-${id}"]:checked`);
            if (!selectedRadio) {
                alert('Please select a reason');
                return;
            }

            let reason = selectedRadio.value;
            if (reason === '__other__') {
                reason = document.getElementById(`reason-other-${id}`).value.trim();
                if (!reason) {
                    alert('Please enter a custom reason');
                    return;
                }
            }

            const note = document.getElementById(`note-${id}`).value.trim();

            try {
                const res = await fetch(`/api/projects/${currentProject}/decisions/${id}`, {
                    method: 'PUT',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({status: 'confirmed', reason, note})
                });
                if (res.ok) {
                    loadDecisions();
                } else {
                    alert('Failed to confirm decision');
                }
            } catch (e) {
                alert('Error: ' + e.message);
            }
        }

        async function skipDecision(id) {
            try {
                const res = await fetch(`/api/projects/${currentProject}/decisions/${id}`, {
                    method: 'PUT',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({status: 'skipped'})
                });
                if (res.ok) {
                    loadDecisions();
                }
            } catch (e) {
                console.error('Error skipping decision:', e);
            }
        }

        async function deleteDecision(id) {
            if (!confirm('Delete this decision?')) return;
            try {
                const res = await fetch(`/api/projects/${currentProject}/decisions/${id}`, {method: 'DELETE'});
                if (res.ok) {
                    loadDecisions();
                }
            } catch (e) {
                console.error('Error deleting decision:', e);
            }
        }

        function filterDecisions(status) {
            currentDecisionFilter = status;
            // Update button styles
            document.getElementById('filter-pending-btn').style.background = status === 'pending' ? '#e94560' : '#333';
            document.getElementById('filter-confirmed-btn').style.background = status === 'confirmed' ? '#4ad9a0' : '#333';
            document.getElementById('filter-all-btn').style.background = status === '' ? '#4a90d9' : '#333';
            loadDecisions();
        }

        function searchDecisionsDebounced() {
            clearTimeout(searchDecisionsTimeout);
            searchDecisionsTimeout = setTimeout(searchDecisions, 300);
        }

        async function searchDecisions() {
            const query = document.getElementById('decision-search').value.trim();
            if (!query || !currentProject) {
                loadDecisions();
                return;
            }

            try {
                const res = await fetch(`/api/projects/${currentProject}/decisions/search?q=${encodeURIComponent(query)}`);
                const data = await res.json();

                document.getElementById('pending-decisions-list').innerHTML = '<div style="color: #888; padding: 10px;">Search results:</div>';
                document.getElementById('confirmed-decisions-list').innerHTML = data.decisions.length > 0
                    ? data.decisions.map(d => renderConfirmedDecision(d)).join('')
                    : '<div style="color: #888; padding: 10px;">No matching decisions</div>';
            } catch (e) {
                console.error('Search error:', e);
            }
        }

        // 加载应用配置
        async function loadAppConfig() {
            try {
                const res = await fetch('/api/config');
                const data = await res.json();
                const cfg = data.config || {};
                const defaults = data.defaults || {};
                appConfig.summary_max_chars_total = parseInt(cfg.summary_max_chars_total || defaults.summary_max_chars_total || 8000);
                appConfig.search_result_preview_length = parseInt(cfg.search_result_preview_length || defaults.search_result_preview_length || 500);
                appConfig.dashboard_refresh_interval = parseInt(cfg.dashboard_refresh_interval || defaults.dashboard_refresh_interval || 5000);
                console.log('App config loaded:', appConfig);
            } catch (e) {
                console.error('Failed to load config:', e);
            }
        }

        // 事件通知系统
        let lastEventTime = 0;
        let displayedEvents = new Set();

        async function pollEvents() {
            try {
                const res = await fetch('/api/events');
                const data = await res.json();
                const events = data.events || [];

                const panel = document.getElementById('event-panel');

                for (const evt of events) {
                    const evtId = `${evt.type}-${evt.timestamp}`;
                    if (displayedEvents.has(evtId)) continue;
                    displayedEvents.add(evtId);

                    // 根据事件类型选择颜色和图标
                    let icon = '📌', bgColor = '#16213e', borderColor = '#4a90d9';
                    if (evt.type === 'summary' || evt.type === 'summary_done') {
                        icon = '📝'; borderColor = '#e94560';
                    } else if (evt.type === 'knowledge' || evt.type === 'knowledge_done') {
                        icon = '🧠'; borderColor = '#00d9ff';
                    } else if (evt.type === 'embedding') {
                        icon = '🔢'; borderColor = '#d9a04a';
                    } else if (evt.type === 'session' || evt.type === 'session_end') {
                        icon = '🚀'; borderColor = '#4ad9a0';
                    } else if (evt.type === 'message') {
                        icon = '💬'; borderColor = '#9a4ad9';
                    } else if (evt.type === 'error') {
                        icon = '❌'; borderColor = '#ff4444'; bgColor = '#2a1a1a';
                    }

                    const toast = document.createElement('div');
                    toast.style.cssText = `
                        background: ${bgColor}; border-left: 3px solid ${borderColor};
                        padding: 10px 12px; border-radius: 6px; margin-bottom: 8px;
                        box-shadow: 0 4px 12px rgba(0,0,0,0.4); animation: slideIn 0.3s ease;
                        font-size: 0.85em;
                    `;
                    toast.innerHTML = `
                        <div style="display: flex; justify-content: space-between; align-items: center;">
                            <span>${icon} <strong style="color: ${borderColor};">${escapeHtml(evt.message)}</strong></span>
                            <span style="color: #666; font-size: 0.8em;">${evt.time_str || ''}</span>
                        </div>
                        ${evt.details ? `<div style="color: #888; font-size: 0.8em; margin-top: 3px;">${escapeHtml(evt.details)}</div>` : ''}
                    `;
                    panel.appendChild(toast);

                    // 自动移除
                    setTimeout(() => {
                        toast.style.opacity = '0';
                        toast.style.transform = 'translateX(-20px)';
                        toast.style.transition = 'all 0.3s ease';
                        setTimeout(() => toast.remove(), 300);
                    }, evt.type.includes('done') ? 3000 : 5000);
                }

                // 清理旧的已显示事件 ID
                if (displayedEvents.size > 100) {
                    displayedEvents = new Set([...displayedEvents].slice(-50));
                }
            } catch (e) {
                // 忽略轮询错误
            }
        }

        // 初始化
        let refreshIntervalId = null;
        let eventPollId = null;
        async function initApp() {
            await loadAppConfig();
            await refreshAll();
            // 设置刷新间隔（0 表示禁用）
            if (appConfig.dashboard_refresh_interval > 0) {
                refreshIntervalId = setInterval(refreshAll, appConfig.dashboard_refresh_interval);
            }
            // 事件轮询（每秒）
            eventPollId = setInterval(pollEvents, 1000);
            pollEvents();
        }
        initApp();
    