"use strict";
var __createBinding = (this && this.__createBinding) || (Object.create ? (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    var desc = Object.getOwnPropertyDescriptor(m, k);
    if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
      desc = { enumerable: true, get: function() { return m[k]; } };
    }
    Object.defineProperty(o, k2, desc);
}) : (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    o[k2] = m[k];
}));
var __setModuleDefault = (this && this.__setModuleDefault) || (Object.create ? (function(o, v) {
    Object.defineProperty(o, "default", { enumerable: true, value: v });
}) : function(o, v) {
    o["default"] = v;
});
var __importStar = (this && this.__importStar) || (function () {
    var ownKeys = function(o) {
        ownKeys = Object.getOwnPropertyNames || function (o) {
            var ar = [];
            for (var k in o) if (Object.prototype.hasOwnProperty.call(o, k)) ar[ar.length] = k;
            return ar;
        };
        return ownKeys(o);
    };
    return function (mod) {
        if (mod && mod.__esModule) return mod;
        var result = {};
        if (mod != null) for (var k = ownKeys(mod), i = 0; i < k.length; i++) if (k[i] !== "default") __createBinding(result, mod, k[i]);
        __setModuleDefault(result, mod);
        return result;
    };
})();
Object.defineProperty(exports, "__esModule", { value: true });
exports.activate = activate;
exports.deactivate = deactivate;
const cp = __importStar(require("child_process"));
const fs = __importStar(require("fs"));
const path = __importStar(require("path"));
const vscode = __importStar(require("vscode"));
const bridge_1 = require("./bridge");
const protocol_1 = require("./common/protocol");
function activate(context) {
    const output = vscode.window.createOutputChannel('CodeAgent');
    output.appendLine('CodeAgent extension activated.');
    context.subscriptions.push(output);
    context.subscriptions.push(vscode.window.registerTreeDataProvider('codeagent.sidebar', new CodeAgentSidebarProvider()));
    context.subscriptions.push(vscode.commands.registerCommand('codeagent.openPanel', () => {
        output.appendLine('Command codeagent.openPanel invoked.');
        try {
            CodeAgentPanel.show(context, output);
        }
        catch (error) {
            const message = error instanceof Error ? error.message : String(error);
            output.appendLine(`Failed to open CodeAgent panel: ${message}`);
            void vscode.window.showErrorMessage(`CodeAgent 面板打开失败：${message}`);
        }
    }));
}
function deactivate() {
    CodeAgentPanel.current?.dispose();
}
class CodeAgentSidebarProvider {
    getTreeItem(element) {
        return element;
    }
    getChildren() {
        return [];
    }
}
class CodeAgentPanel {
    context;
    output;
    static current;
    panel;
    disposables = [];
    child;
    parser = new bridge_1.BridgeLineParser();
    static show(context, output) {
        if (CodeAgentPanel.current) {
            CodeAgentPanel.current.panel.reveal(vscode.ViewColumn.One);
            output.appendLine('Revealed existing CodeAgent panel.');
            return;
        }
        output.appendLine('Creating CodeAgent webview panel.');
        const panel = vscode.window.createWebviewPanel('codeagent.panel', 'CodeAgent', vscode.ViewColumn.One, {
            enableScripts: true,
            retainContextWhenHidden: true,
            localResourceRoots: [
                vscode.Uri.joinPath(context.extensionUri, 'dist', 'webview')
            ]
        });
        CodeAgentPanel.current = new CodeAgentPanel(context, panel, output);
        output.appendLine('CodeAgent webview panel created.');
    }
    constructor(context, panel, output) {
        this.context = context;
        this.output = output;
        this.panel = panel;
        this.panel.webview.html = this.renderHtml();
        this.panel.onDidDispose(() => this.dispose(), null, this.disposables);
        this.panel.webview.onDidReceiveMessage((message) => this.handleMessage(message), null, this.disposables);
        void vscode.window.showInformationMessage('CodeAgent 面板已打开。');
    }
    dispose() {
        if (CodeAgentPanel.current === this) {
            CodeAgentPanel.current = undefined;
        }
        if (this.child) {
            this.stopChildProcess();
            this.child = undefined;
        }
        while (this.disposables.length) {
            this.disposables.pop()?.dispose();
        }
    }
    async handleMessage(message) {
        try {
            if (message.type === 'ready') {
                this.output.appendLine('Webview frontend is ready.');
                await this.postInit();
                return;
            }
            if (message.type === 'chooseFiles') {
                await this.chooseFiles();
                return;
            }
            if (message.type === 'openFile') {
                await this.openFile(message.path);
                return;
            }
            if (message.type === 'startRun') {
                await this.startRun(message.form);
                return;
            }
            if (message.type === 'approvalDecision') {
                this.sendApprovalDecision(message);
                return;
            }
            if (message.type === 'cancelRun') {
                this.stopChildProcess();
                this.post({ type: 'bridgeStderr', text: 'CodeAgent 运行已请求停止。' });
            }
        }
        catch (error) {
            const detail = error instanceof Error ? error.stack ?? error.message : String(error);
            this.output.appendLine(`Webview message handling failed: ${detail}`);
            this.post({
                type: 'extensionError',
                message: error instanceof Error ? error.message : String(error)
            });
        }
    }
    async postInit() {
        const workspacePath = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? '';
        const workspaceDirectories = await collectWorkspaceDirectories();
        this.post({
            type: 'init',
            payload: {
                workspacePath,
                outputDir: workspacePath ? path.join(workspacePath, 'codeagent_runs') : 'codeagent_runs',
                workspaceDirectories,
                modelChoices: protocol_1.MODEL_CHOICES,
                defaultModel: protocol_1.MODEL_CHOICES[0]
            }
        });
    }
    async chooseFiles() {
        const workspacePath = vscode.workspace.workspaceFolders?.[0]?.uri;
        const uris = await vscode.window.showOpenDialog({
            canSelectFiles: true,
            canSelectFolders: false,
            canSelectMany: true,
            defaultUri: workspacePath,
            title: '选择 CodeAgent 输入材料'
        });
        if (!uris?.length) {
            return;
        }
        this.post({
            type: 'filesSelected',
            files: uris.map((uri) => (0, bridge_1.materialFromPath)(uri.fsPath))
        });
    }
    async openFile(filePath) {
        const uri = vscode.Uri.file(filePath);
        const stat = await fs.promises.stat(filePath).catch(() => undefined);
        if (stat?.isDirectory()) {
            await vscode.commands.executeCommand('revealFileInOS', uri);
            return;
        }
        const document = await vscode.workspace.openTextDocument(uri);
        await vscode.window.showTextDocument(document, { preview: true });
    }
    async startRun(form) {
        if (this.child) {
            throw new Error('CodeAgent is already running.');
        }
        const resolvedForm = resolveFormPaths(form);
        const taskConfig = (0, bridge_1.buildTaskConfig)(resolvedForm);
        const taskDir = path.join(this.context.globalStorageUri.fsPath, 'tasks');
        await fs.promises.mkdir(taskDir, { recursive: true });
        const taskPath = path.join(taskDir, `codeagent-task-${Date.now()}.json`);
        await fs.promises.writeFile(taskPath, JSON.stringify(taskConfig, null, 2), 'utf8');
        this.output.appendLine(`Task config written: ${taskPath}`);
        const pythonPath = vscode.workspace
            .getConfiguration('codeagent')
            .get('pythonPath', 'python');
        this.parser = new bridge_1.BridgeLineParser();
        this.child = cp.spawn(pythonPath, ['-m', 'codeagent', 'vscode-run', '--config', taskPath], {
            cwd: resolvedForm.projectPath || vscode.workspace.workspaceFolders?.[0]?.uri.fsPath,
            env: {
                ...process.env,
                PYTHONIOENCODING: 'utf-8',
                PYTHONUTF8: '1'
            },
            stdio: 'pipe'
        });
        this.output.appendLine(`Spawned CodeAgent bridge: ${pythonPath} -m codeagent vscode-run --config ${taskPath}`);
        this.child.stdout.on('data', (chunk) => {
            for (const event of this.parser.push(chunk)) {
                this.post({ type: 'bridgeEvent', event });
            }
        });
        this.child.stderr.on('data', (chunk) => {
            this.post({ type: 'bridgeStderr', text: chunk.toString('utf8') });
        });
        this.child.on('error', (error) => {
            this.post({ type: 'extensionError', message: error.message });
            this.child = undefined;
        });
        this.child.on('exit', (code, signal) => {
            this.post({ type: 'processExited', code, signal });
            this.child = undefined;
        });
    }
    sendApprovalDecision(message) {
        if (!this.child) {
            this.post({ type: 'extensionError', message: 'CodeAgent process is not running.' });
            return;
        }
        this.child.stdin.write((0, bridge_1.serializeApprovalDecision)(message.interruptId, message.decisionType, message.comment, message.editedPayload));
    }
    stopChildProcess() {
        const child = this.child;
        if (!child) {
            return;
        }
        if (process.platform === 'win32' && child.pid) {
            cp.execFile('taskkill', ['/pid', String(child.pid), '/T', '/F'], (error) => {
                if (error) {
                    this.output.appendLine(`taskkill failed: ${error.message}`);
                    child.kill();
                }
            });
        }
        else {
            child.kill('SIGTERM');
        }
    }
    post(message) {
        void this.panel.webview.postMessage(message);
    }
    renderHtml() {
        const webview = this.panel.webview;
        const webviewDist = vscode.Uri.joinPath(this.context.extensionUri, 'dist', 'webview');
        const scriptUri = webview.asWebviewUri(vscode.Uri.joinPath(webviewDist, 'main.js'));
        const styleUri = webview.asWebviewUri(vscode.Uri.joinPath(webviewDist, 'style.css'));
        const nonce = makeNonce();
        const scriptSrc = escapeJsString(scriptUri.toString());
        this.output.appendLine(`Webview script URI: ${scriptUri.toString()}`);
        this.output.appendLine(`Webview style URI: ${styleUri.toString()}`);
        return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src ${webview.cspSource} data:; style-src ${webview.cspSource} 'unsafe-inline'; script-src ${webview.cspSource} 'nonce-${nonce}';">
  <link href="${styleUri}" rel="stylesheet">
  <title>CodeAgent</title>
</head>
<body>
  <div id="root">
    <div style="padding: 18px; color: var(--vscode-editor-foreground); font-family: var(--vscode-font-family);">
      CodeAgent 面板正在加载...
    </div>
  </div>
  <script nonce="${nonce}">
    (function () {
      const root = document.getElementById('root');
      function showError(message) {
        if (!root) {
          return;
        }
        root.innerHTML = '';
        const box = document.createElement('div');
        box.style.padding = '18px';
        box.style.color = 'var(--vscode-errorForeground)';
        box.style.fontFamily = 'var(--vscode-font-family)';
        box.textContent = 'CodeAgent 前端脚本加载失败：' + message;
        root.appendChild(box);
      }
      window.addEventListener('error', function (event) {
        showError(event.message || '未知脚本错误');
      });
      window.addEventListener('unhandledrejection', function (event) {
        const reason = event.reason && (event.reason.message || String(event.reason));
        showError(reason || '未知异步错误');
      });
      const script = document.createElement('script');
      script.src = '${scriptSrc}';
      script.nonce = '${nonce}';
      script.onerror = function () {
        showError('main.js 无法加载');
      };
      document.body.appendChild(script);
    }());
  </script>
</body>
</html>`;
    }
}
function makeNonce() {
    const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
    let nonce = '';
    for (let index = 0; index < 32; index += 1) {
        nonce += chars.charAt(Math.floor(Math.random() * chars.length));
    }
    return nonce;
}
function escapeJsString(value) {
    return value.replace(/\\/g, '\\\\').replace(/'/g, "\\'");
}
async function collectWorkspaceDirectories() {
    const folders = vscode.workspace.workspaceFolders ?? [];
    const options = [];
    const seen = new Set();
    const folder = folders[0];
    if (folder) {
        await collectDirectoryOptions(folder.uri.fsPath, options, seen);
    }
    return options;
}
async function collectDirectoryOptions(rootPath, options, seen) {
    const maxDepth = 3;
    const maxOptions = 240;
    const skipNames = new Set([
        '.git',
        '.hg',
        '.svn',
        '.mypy_cache',
        '.pytest_cache',
        '.ruff_cache',
        '.venv',
        'venv',
        'env',
        '__pycache__',
        'node_modules',
        'dist',
        'build',
        'coverage'
    ]);
    const addOption = (relativePath) => {
        const normalizedRelative = relativePath === '.'
            ? '.'
            : relativePath.replace(/\//g, path.sep);
        const key = normalizedRelative.toLowerCase();
        if (!seen.has(key)) {
            seen.add(key);
            options.push({
                label: normalizedRelative,
                path: path.resolve(rootPath, normalizedRelative)
            });
        }
    };
    addOption('.');
    const visit = async (directoryPath, depth) => {
        if (depth >= maxDepth || options.length >= maxOptions) {
            return;
        }
        let entries;
        try {
            entries = await fs.promises.readdir(directoryPath, { withFileTypes: true });
        }
        catch {
            return;
        }
        const directories = entries
            .filter((entry) => entry.isDirectory() && !skipNames.has(entry.name))
            .sort((left, right) => left.name.localeCompare(right.name, 'zh-Hans-CN'));
        for (const entry of directories) {
            if (options.length >= maxOptions) {
                return;
            }
            const childPath = path.join(directoryPath, entry.name);
            const relative = path.relative(rootPath, childPath) || '.';
            addOption(relative);
            await visit(childPath, depth + 1);
        }
    };
    await visit(rootPath, 0);
}
function resolveFormPaths(form) {
    return {
        ...form,
        projectPath: resolveWorkspacePath(form.projectPath),
        outputDir: resolveWorkspacePath(form.outputDir)
    };
}
function resolveWorkspacePath(value) {
    const trimmed = value.trim();
    if (!trimmed) {
        return trimmed;
    }
    if (path.isAbsolute(trimmed)) {
        return path.normalize(trimmed);
    }
    const workspacePath = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
    return workspacePath ? path.resolve(workspacePath, trimmed) : path.resolve(trimmed);
}
//# sourceMappingURL=extension.js.map