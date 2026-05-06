import * as vscode from 'vscode';
import { KwcodeServerClient } from './server-client';
import { KwcodePanel } from './panel';

let serverClient: KwcodeServerClient | undefined;
let panel: KwcodePanel | undefined;

export function activate(context: vscode.ExtensionContext) {
    const config = vscode.workspace.getConfiguration('kwcode');
    const serverUrl = config.get<string>('serverUrl', 'http://127.0.0.1:7355');

    serverClient = new KwcodeServerClient(serverUrl);

    // Command: Open KwCode panel
    const openPanelCmd = vscode.commands.registerCommand('kwcode.openPanel', () => {
        if (!panel) {
            panel = new KwcodePanel(context, serverClient!);
        }
        panel.reveal();
    });
    context.subscriptions.push(openPanelCmd);

    // Command: Submit task (from command palette)
    const submitTaskCmd = vscode.commands.registerCommand('kwcode.submitTask', async () => {
        const task = await vscode.window.showInputBox({
            prompt: '输入任务描述',
            placeHolder: '例如: 修复登录bug',
        });
        if (task && panel) {
            panel.submitTask(task);
        } else if (task && !panel) {
            panel = new KwcodePanel(context, serverClient!);
            panel.reveal();
            // Small delay to let webview initialize
            setTimeout(() => panel!.submitTask(task), 500);
        }
    });
    context.subscriptions.push(submitTaskCmd);

    // Auto-refresh rig.json on file save
    const autoRefresh = config.get<boolean>('autoRefreshRig', true);
    if (autoRefresh) {
        const saveListener = vscode.workspace.onDidSaveTextDocument(async (doc) => {
            // Only refresh for code files, not config/lock files
            const ext = doc.fileName.split('.').pop()?.toLowerCase();
            const codeExts = ['py', 'js', 'ts', 'tsx', 'go', 'rs', 'java', 'cs'];
            if (ext && codeExts.includes(ext)) {
                try {
                    await serverClient?.refreshRig();
                } catch {
                    // Silent failure - server might not be running
                }
            }
        });
        context.subscriptions.push(saveListener);
    }

    // Status bar item showing connection state
    const statusBar = vscode.window.createStatusBarItem(
        vscode.StatusBarAlignment.Right, 100
    );
    statusBar.command = 'kwcode.openPanel';
    statusBar.text = '$(circle-outline) KwCode';
    statusBar.tooltip = 'KwCode: Click to open panel';
    statusBar.show();
    context.subscriptions.push(statusBar);

    // Check server connection periodically
    const checkConnection = async () => {
        const connected = await serverClient?.checkHealth();
        if (connected) {
            statusBar.text = '$(circle-filled) KwCode';
            statusBar.tooltip = `KwCode: Connected to ${serverUrl}`;
        } else {
            statusBar.text = '$(circle-outline) KwCode';
            statusBar.tooltip = 'KwCode: Server not running. Run: kwcode serve';
        }
    };

    checkConnection();
    const interval = setInterval(checkConnection, 30000);
    context.subscriptions.push({ dispose: () => clearInterval(interval) });
}

export function deactivate() {
    panel?.dispose();
    panel = undefined;
    serverClient = undefined;
}
