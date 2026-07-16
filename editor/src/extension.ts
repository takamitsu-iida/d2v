import * as vscode from "vscode";

/** /api/focus/preview のレスポンス。 */
interface FocusPreviewResponse {
  svg: string | null;
  focus: string[];
  context: string;
  hops: number;
  device_lines: Record<string, number>;
  not_found: string[];
  message: string | null;
}

/** iida-network-model YAML かどうかを緩く判定する。 */
function looksLikeTopology(doc: vscode.TextDocument): boolean {
  if (doc.languageId !== "yaml") {
    return false;
  }
  return /^\s*network-model\s*:/m.test(doc.getText());
}

class FocusPreviewPanel {
  public static current: FocusPreviewPanel | undefined;

  private readonly panel: vscode.WebviewPanel;
  private readonly disposables: vscode.Disposable[] = [];
  /** 直近で解決した device-id → 定義行（1 始まり）。行ジャンプに使う。 */
  private deviceLines: Record<string, number> = {};
  /** 追従対象のエディタ（プレビューを開いたときのアクティブ YAML）。 */
  private sourceUri: vscode.Uri | undefined;

  static createOrShow(context: vscode.ExtensionContext): FocusPreviewPanel {
    const column = vscode.ViewColumn.Beside;
    if (FocusPreviewPanel.current) {
      FocusPreviewPanel.current.panel.reveal(column, true);
      return FocusPreviewPanel.current;
    }
    const panel = vscode.window.createWebviewPanel(
      "d2vFocusPreview",
      "d2v フォーカスプレビュー",
      { viewColumn: column, preserveFocus: true },
      { enableScripts: true, retainContextWhenHidden: true }
    );
    FocusPreviewPanel.current = new FocusPreviewPanel(panel, context);
    return FocusPreviewPanel.current;
  }

  private constructor(
    panel: vscode.WebviewPanel,
    private readonly context: vscode.ExtensionContext
  ) {
    this.panel = panel;
    this.panel.webview.html = this.buildHtml();

    this.panel.onDidDispose(() => this.dispose(), null, this.disposables);

    this.panel.webview.onDidReceiveMessage(
      (msg) => this.onMessage(msg),
      null,
      this.disposables
    );
  }

  /** Webview からのメッセージを処理する。 */
  private async onMessage(msg: any): Promise<void> {
    switch (msg?.type) {
      case "jump":
        await this.jumpToDevice(String(msg.deviceId ?? ""));
        break;
      case "setHops":
        await vscode.workspace
          .getConfiguration("d2v")
          .update("hops", Number(msg.hops), vscode.ConfigurationTarget.Global);
        this.refreshFromActiveEditor();
        break;
      case "setFollow":
        await vscode.workspace
          .getConfiguration("d2v")
          .update("follow", Boolean(msg.follow), vscode.ConfigurationTarget.Global);
        break;
      case "refresh":
        this.refreshFromActiveEditor();
        break;
    }
  }

  /** device-id の定義行へカーソルを移動する。 */
  private async jumpToDevice(deviceId: string): Promise<void> {
    const line = this.deviceLines[deviceId];
    if (!line || !this.sourceUri) {
      return;
    }
    const doc = await vscode.workspace.openTextDocument(this.sourceUri);
    const editor = await vscode.window.showTextDocument(doc, {
      viewColumn: vscode.ViewColumn.One,
      preserveFocus: false,
    });
    const pos = new vscode.Position(Math.max(0, line - 1), 0);
    editor.selection = new vscode.Selection(pos, pos);
    editor.revealRange(
      new vscode.Range(pos, pos),
      vscode.TextEditorRevealType.InCenter
    );
  }

  /** アクティブなエディタの状態からプレビューを更新する。 */
  refreshFromActiveEditor(): void {
    const editor = vscode.window.activeTextEditor;
    if (editor && looksLikeTopology(editor.document)) {
      this.sourceUri = editor.document.uri;
    }
    void this.update();
  }

  /** カーソル位置・本文を API に送ってプレビューを更新する。 */
  private async update(): Promise<void> {
    const editor = this.sourceUri
      ? vscode.window.visibleTextEditors.find(
          (e) => e.document.uri.toString() === this.sourceUri!.toString()
        ) ?? vscode.window.activeTextEditor
      : vscode.window.activeTextEditor;

    if (!editor || !looksLikeTopology(editor.document)) {
      this.post({ type: "status", message: "iida-network-model の YAML を開いてください。" });
      return;
    }
    this.sourceUri = editor.document.uri;

    const cfg = vscode.workspace.getConfiguration("d2v");
    const serverUrl = String(cfg.get("serverUrl", "http://127.0.0.1:8000")).replace(/\/$/, "");
    const hops = Number(cfg.get("hops", 1));
    const line = editor.selection.active.line + 1; // 1 始まり
    const yamlText = editor.document.getText();

    this.post({ type: "loading", hops });

    let res: FocusPreviewResponse;
    try {
      const r = await fetch(`${serverUrl}/api/focus/preview`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ source: "text", yaml_text: yamlText, line, hops }),
      });
      if (r.status === 400) {
        // 編集途中の不正 YAML: 直前の図を保持しつつ状態だけ通知
        this.post({ type: "status", message: "解析待ち（YAML が未完成です）", keepSvg: true });
        return;
      }
      if (!r.ok) {
        this.post({ type: "error", message: `サーバーエラー: HTTP ${r.status}` });
        return;
      }
      res = (await r.json()) as FocusPreviewResponse;
    } catch (e) {
      this.post({
        type: "error",
        message: `d2v serve に接続できません（${serverUrl}）。\n\`python main.py serve\` を起動してください。`,
      });
      return;
    }

    this.deviceLines = res.device_lines ?? {};
    this.post({ type: "preview", data: res });
  }

  private post(msg: any): void {
    void this.panel.webview.postMessage(msg);
  }

  private buildHtml(): string {
    const webview = this.panel.webview;
    const nonce = getNonce();
    const scriptUri = webview.asWebviewUri(
      vscode.Uri.joinPath(this.context.extensionUri, "media", "main.js")
    );
    const styleUri = webview.asWebviewUri(
      vscode.Uri.joinPath(this.context.extensionUri, "media", "main.css")
    );
    const csp = [
      `default-src 'none'`,
      `img-src ${webview.cspSource} https: data:`,
      `style-src ${webview.cspSource} 'unsafe-inline'`,
      `script-src 'nonce-${nonce}'`,
      `font-src ${webview.cspSource}`,
    ].join("; ");

    return `<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8" />
  <meta http-equiv="Content-Security-Policy" content="${csp}" />
  <link href="${styleUri}" rel="stylesheet" />
  <title>d2v フォーカスプレビュー</title>
</head>
<body>
  <div id="toolbar">
    <label>ホップ
      <input type="range" id="hops" min="1" max="3" step="1" value="1" />
      <span id="hops-val">1</span>
    </label>
    <label><input type="checkbox" id="follow" checked /> 追従</label>
    <button id="refresh">再読込</button>
    <span id="status"></span>
  </div>
  <div id="diagram"><div class="hint">iida-network-model の YAML を開くとプレビューが表示されます。</div></div>
  <script nonce="${nonce}" src="${scriptUri}"></script>
</body>
</html>`;
  }

  dispose(): void {
    FocusPreviewPanel.current = undefined;
    this.panel.dispose();
    while (this.disposables.length) {
      this.disposables.pop()?.dispose();
    }
  }
}

function getNonce(): string {
  let text = "";
  const chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
  for (let i = 0; i < 32; i++) {
    text += chars.charAt(Math.floor(Math.random() * chars.length));
  }
  return text;
}

/** d2v.serverUrl を末尾スラッシュなしで返す。 */
function serverUrl(): string {
  return String(
    vscode.workspace.getConfiguration("d2v").get("serverUrl", "http://127.0.0.1:8000")
  ).replace(/\/$/, "");
}

// ---------------------------------------------------------------------------
// design lint（diagnostics / 波線）
// ---------------------------------------------------------------------------

interface LintIssue {
  rule: string;
  severity: string;
  message: string;
  targets: string[];
  line: number | null;
}

function severityOf(sev: string): vscode.DiagnosticSeverity {
  switch (sev) {
    case "error":
      return vscode.DiagnosticSeverity.Error;
    case "warning":
      return vscode.DiagnosticSeverity.Warning;
    default:
      return vscode.DiagnosticSeverity.Information;
  }
}

async function runLint(
  doc: vscode.TextDocument,
  collection: vscode.DiagnosticCollection
): Promise<void> {
  if (!looksLikeTopology(doc)) {
    collection.delete(doc.uri);
    return;
  }
  let issues: LintIssue[];
  try {
    const r = await fetch(`${serverUrl()}/api/lint`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source: "text", yaml_text: doc.getText() }),
    });
    if (!r.ok) {
      // 400（編集途中の不正 YAML）等では既存の波線を保持する
      return;
    }
    const data = (await r.json()) as { issues: LintIssue[] };
    issues = data.issues ?? [];
  } catch {
    // サーバー未起動時は診断をクリアせず沈黙する
    return;
  }

  const diagnostics: vscode.Diagnostic[] = issues.map((iss) => {
    const lineIdx = Math.max(0, (iss.line ?? 1) - 1);
    const safeLine = Math.min(lineIdx, Math.max(0, doc.lineCount - 1));
    const range = doc.lineAt(safeLine).range;
    const diag = new vscode.Diagnostic(
      range,
      `${iss.message}${iss.targets.length ? ` [${iss.targets.join(", ")}]` : ""}`,
      severityOf(iss.severity)
    );
    diag.source = "d2v";
    diag.code = iss.rule;
    return diag;
  });
  collection.set(doc.uri, diagnostics);
}

// ---------------------------------------------------------------------------
// 補完（device-id / interface-id）
// ---------------------------------------------------------------------------

function collectValues(text: string, key: string): string[] {
  const re = new RegExp(`${key}\\s*:\\s*"?([A-Za-z0-9_./:\\-]+)`, "g");
  const seen = new Set<string>();
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    seen.add(m[1]);
  }
  return [...seen].sort();
}

const completionProvider: vscode.CompletionItemProvider = {
  provideCompletionItems(document, position) {
    if (!looksLikeTopology(document)) {
      return undefined;
    }
    const linePrefix = document.lineAt(position.line).text.slice(0, position.character);
    const keyMatch = /(^|\s|-)\s*(device-id|interface-id)\s*:\s*"?[^"\n]*$/.exec(linePrefix);
    if (!keyMatch) {
      return undefined;
    }
    const key = keyMatch[2];
    const values = collectValues(document.getText(), key);
    return values.map((v) => {
      const item = new vscode.CompletionItem(v, vscode.CompletionItemKind.Value);
      item.detail = `d2v ${key}`;
      return item;
    });
  },
};

export function activate(context: vscode.ExtensionContext): void {
  let debounce: NodeJS.Timeout | undefined;

  const scheduleUpdate = () => {
    const panel = FocusPreviewPanel.current;
    if (!panel) {
      return;
    }
    const cfg = vscode.workspace.getConfiguration("d2v");
    if (!cfg.get("follow", true)) {
      return;
    }
    const delay = Number(cfg.get("debounceMs", 250));
    if (debounce) {
      clearTimeout(debounce);
    }
    debounce = setTimeout(() => panel.refreshFromActiveEditor(), delay);
  };

  // design lint（波線）
  const lintCollection = vscode.languages.createDiagnosticCollection("d2v");
  context.subscriptions.push(lintCollection);
  const lint = (doc: vscode.TextDocument) => void runLint(doc, lintCollection);

  context.subscriptions.push(
    vscode.commands.registerCommand("d2v.openFocusPreview", () => {
      const panel = FocusPreviewPanel.createOrShow(context);
      panel.refreshFromActiveEditor();
    }),
    vscode.commands.registerCommand("d2v.toggleFollow", async () => {
      const cfg = vscode.workspace.getConfiguration("d2v");
      const next = !cfg.get("follow", true);
      await cfg.update("follow", next, vscode.ConfigurationTarget.Global);
      vscode.window.showInformationMessage(
        `d2v: カーソル追従を${next ? "ON" : "OFF"}にしました。`
      );
    }),
    vscode.languages.registerCompletionItemProvider(
      { language: "yaml" },
      completionProvider,
      '"',
      " ",
      ":"
    ),
    // design lint: 保存・オープン時に実行
    vscode.workspace.onDidSaveTextDocument(lint),
    vscode.workspace.onDidOpenTextDocument(lint),
    vscode.workspace.onDidCloseTextDocument((doc) => lintCollection.delete(doc.uri)),
    vscode.window.onDidChangeTextEditorSelection((e) => {
      if (looksLikeTopology(e.textEditor.document)) {
        scheduleUpdate();
      }
    }),
    vscode.workspace.onDidChangeTextDocument((e) => {
      const active = vscode.window.activeTextEditor;
      if (active && e.document === active.document && looksLikeTopology(e.document)) {
        scheduleUpdate();
      }
    }),
    vscode.window.onDidChangeActiveTextEditor(() => scheduleUpdate())
  );

  // 起動時にアクティブな YAML を一度 lint する
  if (vscode.window.activeTextEditor) {
    lint(vscode.window.activeTextEditor.document);
  }
}

export function deactivate(): void {
  FocusPreviewPanel.current?.dispose();
}
