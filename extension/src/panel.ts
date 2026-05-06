import * as vscode from 'vscode';
import { KwcodeServerClient } from './server-client';

/**
 * Webview panel for KwCode.
 * Displays event log and task input.
 * Thin client — all logic runs on kwcode server.
 */
export class KwcodePanel {
    private panel: vscode.WebviewPanel | undefined;
    private client: KwcodeServerClient;
    private context: vscode.ExtensionContext;

    constructor(context: vscode.ExtensionContext, client: KwcodeServerClient) {
        this.context = context;
        this.client = client;
    }

    reveal() {
        if (this.panel) {
            this.panel.reveal(vscode.ViewColumn.Beside);
            return;
        }

        this.panel = vscode.window.createWebviewPanel(
            'kwcode',
            'KwCode',
            vscode.ViewColumn.Beside,
            {
                enableScripts: true,
                retainContextWhenHidden: true,
            }
        );

        this.panel.webview.html = this.getWebviewContent();

        // Handle messages from webview
        this.panel.webview.onDidReceiveMessage(
            async (message) => {
                switch (message.type) {
                    case 'submitTask':
                        await this.handleTask(message.task);
                        break;
                }
            },
            undefined,
            this.context.subscriptions
        );

        this.panel.onDidDispose(() => {
            this.panel = undefined;
        });
    }

    async submitTask(task: string) {
        if (this.panel) {
            this.panel.webview.postMessage({
                type: 'taskSubmitted',
                task,
            });
            await this.handleTask(task);
        }
    }

    dispose() {
        this.panel?.dispose();
    }

    private async handleTask(task: string) {
        // Check server connection
        const connected = await this.client.checkHealth();
        if (!connected) {
            this.postEvent({
                event: 'task_error',
                error: 'Server 未运行。请先执行: kwcode serve',
            });
            return;
        }

        // Submit task
        const taskId = await this.client.submitTask(task);
        if (!taskId) {
            this.postEvent({
                event: 'task_error',
                error: '任务提交失败',
            });
            return;
        }

        // Stream events
        this.client.streamEvents(
            taskId,
            (event) => this.postEvent(event),
            () => { /* done */ },
            (err) => {
                this.postEvent({
                    event: 'task_error',
                    error: `连接错误: ${err.message}`,
                });
            }
        );
    }

    private postEvent(event: Record<string, unknown>) {
        this.panel?.webview.postMessage({ type: 'event', data: event });
    }

    private getWebviewContent(): string {
        return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>KwCode</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: var(--vscode-font-family);
            font-size: var(--vscode-font-size);
            color: var(--vscode-foreground);
            background: var(--vscode-editor-background);
            height: 100vh;
            display: flex;
            flex-direction: column;
        }
        #header {
            padding: 8px 12px;
            border-bottom: 1px solid var(--vscode-panel-border);
            font-weight: bold;
            font-size: 13px;
        }
        #event-log {
            flex: 1;
            overflow-y: auto;
            padding: 8px 12px;
            font-family: var(--vscode-editor-font-family);
            font-size: 12px;
            line-height: 1.6;
        }
        .event-line {
            padding: 2px 0;
            white-space: pre-wrap;
            word-break: break-all;
        }
        .event-line.phase { font-weight: bold; margin-top: 8px; }
        .event-line.success { color: var(--vscode-testing-iconPassed); }
        .event-line.error { color: var(--vscode-testing-iconFailed); }
        .event-line.info { color: var(--vscode-textLink-foreground); }
        .event-line.dim { opacity: 0.6; }
        .event-line.task-input {
            color: var(--vscode-textLink-foreground);
            font-weight: bold;
            margin-top: 12px;
        }
        #input-area {
            padding: 8px 12px;
            border-top: 1px solid var(--vscode-panel-border);
            display: flex;
            gap: 8px;
        }
        #task-input {
            flex: 1;
            padding: 6px 10px;
            border: 1px solid var(--vscode-input-border);
            background: var(--vscode-input-background);
            color: var(--vscode-input-foreground);
            font-family: var(--vscode-font-family);
            font-size: 13px;
            border-radius: 3px;
            outline: none;
        }
        #task-input:focus {
            border-color: var(--vscode-focusBorder);
        }
        #submit-btn {
            padding: 6px 14px;
            background: var(--vscode-button-background);
            color: var(--vscode-button-foreground);
            border: none;
            border-radius: 3px;
            cursor: pointer;
            font-size: 13px;
        }
        #submit-btn:hover {
            background: var(--vscode-button-hoverBackground);
        }
    </style>
</head>
<body>
    <div id="header">KwCode</div>
    <div id="event-log">
        <div class="event-line dim">输入任务描述开始工作...</div>
    </div>
    <div id="input-area">
        <input type="text" id="task-input" placeholder="输入任务..." />
        <button id="submit-btn">执行</button>
    </div>

    <script>
        const vscode = acquireVsCodeApi();
        const log = document.getElementById('event-log');
        const input = document.getElementById('task-input');
        const btn = document.getElementById('submit-btn');

        const EVENT_ICONS = {
            expert_start: '●',
            reading_file: '  📄',
            file_written: '  ✓',
            applying_patch: '  →',
            test_pass: '  ✓',
            test_fail: '  ✗',
            retry: '🔄',
            circuit_break: '⛔',
            search_start: '🌐',
            search_solution: '💡',
            plan_generated: '📋',
            gate_start: '⚡',
            gate_done: '✓',
            task_completed: '✅',
            task_error: '❌',
        };

        function addLine(text, className) {
            const div = document.createElement('div');
            div.className = 'event-line ' + (className || '');
            div.textContent = text;
            log.appendChild(div);
            log.scrollTop = log.scrollHeight;
        }

        function renderEvent(event) {
            const type = event.event || '';
            const icon = EVENT_ICONS[type] || '·';

            if (type === 'task_completed') {
                const success = event.success;
                const elapsed = (event.elapsed || 0).toFixed(1);
                const files = event.files_modified || [];
                if (success) {
                    addLine(icon + ' 任务完成 (' + elapsed + 's)', 'success phase');
                    files.forEach(f => addLine('  ✓ ' + f, 'success'));
                } else {
                    addLine('❌ 任务失败: ' + (event.error || ''), 'error phase');
                }
            } else if (type === 'task_error') {
                addLine(icon + ' ' + (event.error || '未知错误'), 'error phase');
            } else if (type === 'keepalive') {
                // ignore
            } else {
                const msg = event.path || event.msg || event.cmd || '';
                if (msg) {
                    const isPhase = ['expert_start', 'retry', 'circuit_break', 'plan_generated'].includes(type);
                    addLine(icon + ' ' + msg, isPhase ? 'phase info' : 'dim');
                }
            }
        }

        // Submit task
        function submit() {
            const task = input.value.trim();
            if (!task) return;
            addLine('▶ ' + task, 'task-input');
            input.value = '';
            vscode.postMessage({ type: 'submitTask', task });
        }

        btn.addEventListener('click', submit);
        input.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') submit();
        });

        // Receive messages from extension
        window.addEventListener('message', (e) => {
            const msg = e.data;
            if (msg.type === 'event') {
                renderEvent(msg.data);
            } else if (msg.type === 'taskSubmitted') {
                addLine('▶ ' + msg.task, 'task-input');
            }
        });
    </script>
</body>
</html>`;
    }
}
