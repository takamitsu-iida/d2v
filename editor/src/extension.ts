import * as vscode from "vscode";
import * as path from "path";
import * as fs from "fs";
import { loadYangSchema, YangSchema } from "./yangParser";

/** device / connection ブロックの行範囲と focus_ids。 */
interface FocusSpan {
  start: number;      // 1-based, inclusive
  end: number;        // 1-based, inclusive
  focus_ids: string[];
}

/** /api/focus/preview のレスポンス。 */
interface FocusPreviewResponse {
  svg: string | null;
  focus: string[];
  context: string;
  hops: number;
  device_lines: Record<string, number>;
  not_found: string[];
  message: string | null;
  spans?: FocusSpan[];
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
  /** 直前の API 呼び出し時の YAML テキスト（同一ブロック判定用）。 */
  private lastYamlText: string = "";
  /** 直前の API 呼び出し時の hops 設定。 */
  private lastHops: number = -1;
  /** 直前の API レスポンスの spans（ブロック行範囲＋focus_ids）。 */
  private lastSpans: FocusSpan[] = [];
  /** 直前の API 呼び出し時の focus（ソート済み join、スキップ判定用）。 */
  private lastFocus: string = "";

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

    // 同じブロック内でのカーソル移動は SVG 再生成をスキップ
    if (
      this.lastYamlText !== "" &&
      yamlText === this.lastYamlText &&
      hops === this.lastHops &&
      this.lastSpans.length > 0
    ) {
      // サーバーの resolve_focus と同じ 2 段階ロジック:
      // 1) カーソルを含むスパン、2) 直上フォールバック
      let span = this.lastSpans.find(s => s.start <= line && line <= s.end);
      if (!span) {
        const above = this.lastSpans.filter(s => s.start <= line);
        if (above.length > 0) {
          span = above.reduce((best, s) => s.start > best.start ? s : best);
        }
      }
      if (span && [...span.focus_ids].sort().join(",") === this.lastFocus) {
        return;
      }
    }

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
      this.post({ type: "error", message: `d2v serve に接続できません（${serverUrl}）。` });
      if (!serverUnreachableNotified) {
        serverUnreachableNotified = true;
        vscode.window.showWarningMessage(
          `d2v: サーバーに接続できません（${serverUrl}）`,
          "サーバーを起動",
          "設定を確認"
        ).then((action) => {
          if (action === "サーバーを起動") {
            void vscode.commands.executeCommand("d2v.startServer");
          } else if (action === "設定を確認") {
            void vscode.commands.executeCommand("workbench.action.openSettings", "d2v.serverUrl");
          }
        });
      }
      return;
    }

    this.deviceLines = res.device_lines ?? {};
    this.lastSpans = res.spans ?? [];
    this.lastFocus = [...res.focus].sort().join(",");
    this.lastYamlText = yamlText;
    this.lastHops = hops;
    serverUnreachableNotified = false;
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
      <input type="range" id="hops" min="0" max="3" step="1" value="1" />
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

/** 抑制情報の保存先（ワークスペース単位）。activate で設定する。 */
let extensionContext: vscode.ExtensionContext | undefined;
/** サーバー未起動通知済みフラグ（セッション内で1回のみ表示）。 */
let serverUnreachableNotified = false;
const SUPPRESS_STATE_KEY = "d2v.suppressedLints";

/** diagnostic から元の LintIssue を引くための対応表（Quick Fix 用）。 */
const diagnosticIssues = new WeakMap<vscode.Diagnostic, LintIssue>();

/** uri → 表示中の波線（行と issue）。右クリックメニューの抑制で使う。 */
const lintIndexByUri = new Map<string, { line: number; issue: LintIssue }[]>();

/** rule と対象デバイスの組で抑制キーを作る（行番号に依存しない）。 */
function suppressionKey(iss: Pick<LintIssue, "rule" | "targets">): string {
  return `${iss.rule}|${[...iss.targets].sort().join(",")}`;
}

function getSuppressed(): Set<string> {
  return new Set(
    extensionContext?.workspaceState.get<string[]>(SUPPRESS_STATE_KEY, []) ?? []
  );
}

async function addSuppressed(key: string): Promise<void> {
  const s = getSuppressed();
  s.add(key);
  await extensionContext?.workspaceState.update(SUPPRESS_STATE_KEY, [...s]);
}

async function clearSuppressed(): Promise<void> {
  await extensionContext?.workspaceState.update(SUPPRESS_STATE_KEY, []);
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
  if (!vscode.workspace.getConfiguration("d2v").get("lint.enable", true)) {
    collection.delete(doc.uri);
    lintIndexByUri.delete(doc.uri.toString());
    return;
  }
  if (!looksLikeTopology(doc)) {
    collection.delete(doc.uri);
    lintIndexByUri.delete(doc.uri.toString());
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

  const suppressed = getSuppressed();
  const index: { line: number; issue: LintIssue }[] = [];
  const diagnostics: vscode.Diagnostic[] = issues
    .filter((iss) => !suppressed.has(suppressionKey(iss)))
    .map((iss) => {
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
      diagnosticIssues.set(diag, iss);
      index.push({ line: safeLine, issue: iss });
      return diag;
  });
  collection.set(doc.uri, diagnostics);
  lintIndexByUri.set(doc.uri.toString(), index);
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

// ---------------------------------------------------------------------------
// Quick Fix（波線を個別に非表示にする）
// ---------------------------------------------------------------------------

const lintCodeActionProvider: vscode.CodeActionProvider = {
  provideCodeActions(_document, _range, context) {
    const actions: vscode.CodeAction[] = [];
    for (const diag of context.diagnostics) {
      const iss = diag.source === "d2v" ? diagnosticIssues.get(diag) : undefined;
      if (!iss) {
        continue;
      }
      const label = iss.targets.length
        ? `d2v: この波線を非表示にする（${iss.rule} / ${iss.targets.join(", ")}）`
        : `d2v: この波線を非表示にする（${iss.rule}）`;
      const action = new vscode.CodeAction(label, vscode.CodeActionKind.QuickFix);
      action.diagnostics = [diag];
      action.command = {
        command: "d2v.suppressLint",
        title: "d2v design lint を非表示にする",
        arguments: [suppressionKey(iss)],
      };
      actions.push(action);
    }
    return actions;
  },
};

// ---------------------------------------------------------------------------
// YANG スキーマを使った YAML キー補完・列挙値補完
// ---------------------------------------------------------------------------

function buildYangCompletionProvider(
  schemaRef: { schema: YangSchema | null }
): vscode.CompletionItemProvider {
  return {
    provideCompletionItems(document, position) {
      const schema = schemaRef.schema;
      if (!schema || !looksLikeTopology(document)) return undefined;

      const lineText = document.lineAt(position.line).text;
      const before = lineText.slice(0, position.character);

      // 値補完: 行に「key: 」があってカーソルがその後ろにある場合
      const valueM = /^(\s*)(?:-\s+)?([a-zA-Z0-9_-]+)\s*:\s*[^#\n]*$/.exec(before);
      // キー補完: コロンなし、インデントと部分的なキー名のみ
      const isKeyCompletion =
        !valueM && /^(\s*)(?:-\s+)?[a-zA-Z0-9_-]*$/.test(before);

      if (!valueM && !isKeyCompletion) return undefined;

      // カレント行のコンテンツインデントを計算
      let curIndent: number;
      if (valueM) {
        const hasDash = /^\s*-\s+/.test(before);
        curIndent = valueM[1].length + (hasDash ? 2 : 0);
      } else {
        const dashM = /^(\s*)-\s+/.exec(lineText);
        const spaceM = /^(\s*)/.exec(lineText);
        curIndent = dashM ? dashM[1].length + 2 : spaceM ? spaceM[1].length : 0;
      }

      // 上方向へ歩いて YANG パスを構築
      const parts: string[] = [];
      let trackIndent = curIndent;
      if (valueM) parts.unshift(valueM[2]); // 現在のキーをパスの末尾に追加

      for (let li = position.line - 1; li >= 0 && trackIndent > 0; li--) {
        const l = document.lineAt(li).text;
        if (!l.trim()) continue;

        const listM = /^(\s*)-\s+([a-zA-Z0-9_-]+)\s*:/.exec(l);
        const mapM = /^(\s*)([a-zA-Z0-9_-]+)\s*:/.exec(l);
        const lineKey = listM ? listM[2] : mapM ? mapM[2] : undefined;
        const lineIndent = listM
          ? listM[1].length + 2
          : mapM
          ? mapM[1].length
          : Infinity;

        if (lineKey && lineIndent < trackIndent) {
          parts.unshift(lineKey);
          trackIndent = lineIndent;
        }
      }

      const yangPath = parts.join("/");

      if (valueM) {
        // 列挙値の補完
        const vals = schema.enums.get(yangPath);
        if (!vals?.length) return undefined;
        return vals.map((v) => {
          const item = new vscode.CompletionItem(v, vscode.CompletionItemKind.EnumMember);
          item.detail = `YANG enum`;
          return item;
        });
      } else {
        // キー名の補完
        const kids = schema.children.get(yangPath);
        if (!kids?.length) return undefined;
        return kids.map((name) => {
          const item = new vscode.CompletionItem(name, vscode.CompletionItemKind.Field);
          item.detail = `YANG`;
          const desc = schema.descriptions.get(yangPath ? `${yangPath}/${name}` : name);
          if (desc) item.documentation = new vscode.MarkdownString(desc);
          return item;
        });
      }
    },
  };
}

export function activate(context: vscode.ExtensionContext): void {
  extensionContext = context;
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

  // カーソル行に d2v の波線があるかを context key に反映（右クリックメニューの出し分け）
  const updateLintAtCursor = () => {
    const editor = vscode.window.activeTextEditor;
    const has =
      !!editor &&
      (lintIndexByUri.get(editor.document.uri.toString()) ?? []).some(
        (e) => e.line === editor.selection.active.line
      );
    void vscode.commands.executeCommand("setContext", "d2v.lintAtCursor", has);
  };

  const lint = (doc: vscode.TextDocument) =>
    void runLint(doc, lintCollection).then(updateLintAtCursor);

  context.subscriptions.push(
    vscode.commands.registerCommand("d2v.startServer", () => {
      const cfg = vscode.workspace.getConfiguration("d2v");
      const cmd = String(cfg.get("serverCommand", "uv run python main.py serve"));
      // workspace が editor/ サブディレクトリの場合も main.py を辿って repo root を探す
      const wsPath = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
      let cwd = wsPath;
      if (wsPath) {
        let dir = wsPath;
        for (let i = 0; i < 4; i++) {
          if (fs.existsSync(path.join(dir, "main.py"))) { cwd = dir; break; }
          const parent = path.dirname(dir);
          if (parent === dir) break;
          dir = parent;
        }
      }
      const terminal = vscode.window.createTerminal({ name: "d2v serve", cwd });
      terminal.sendText(cmd);
      terminal.show();
    }),
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
    // Quick Fix から呼ばれる: 指定した波線を非表示にして再 lint する
    vscode.commands.registerCommand("d2v.suppressLint", async (key: string) => {
      if (!key) {
        return;
      }
      await addSuppressed(key);
      vscode.workspace.textDocuments.forEach(lint);
    }),
    vscode.commands.registerCommand("d2v.clearSuppressedLints", async () => {
      await clearSuppressed();
      vscode.workspace.textDocuments.forEach(lint);
      vscode.window.showInformationMessage("d2v: 非表示にした design lint を復元しました。");
    }),
    // 右クリックメニューから呼ばれる: カーソル行の波線を非表示にする
    vscode.commands.registerCommand("d2v.suppressLintAtCursor", async () => {
      const editor = vscode.window.activeTextEditor;
      if (!editor) {
        return;
      }
      const line = editor.selection.active.line;
      const entries = (lintIndexByUri.get(editor.document.uri.toString()) ?? []).filter(
        (e) => e.line === line
      );
      if (entries.length === 0) {
        vscode.window.showInformationMessage("d2v: この行に非表示にできる波線はありません。");
        return;
      }
      let targets = entries;
      if (entries.length > 1) {
        const picked = await vscode.window.showQuickPick(
          [
            ...entries.map((e) => ({
              label: e.issue.rule,
              description: e.issue.targets.join(", "),
              entry: e as { line: number; issue: LintIssue } | undefined,
            })),
            { label: "この行のすべて", description: "", entry: undefined },
          ],
          { placeHolder: "非表示にする波線を選択" }
        );
        if (!picked) {
          return;
        }
        targets = picked.entry ? [picked.entry] : entries;
      }
      for (const e of targets) {
        await addSuppressed(suppressionKey(e.issue));
      }
      vscode.workspace.textDocuments.forEach(lint);
    }),
    vscode.languages.registerCompletionItemProvider(
      { language: "yaml" },
      completionProvider,
      '"',
      " ",
      ":"
    ),
    vscode.languages.registerCodeActionsProvider(
      { language: "yaml" },
      lintCodeActionProvider,
      { providedCodeActionKinds: [vscode.CodeActionKind.QuickFix] }
    ),
    // design lint: 保存・オープン時に実行
    vscode.workspace.onDidSaveTextDocument(lint),
    vscode.workspace.onDidOpenTextDocument(lint),
    vscode.workspace.onDidCloseTextDocument((doc) => {
      lintCollection.delete(doc.uri);
      lintIndexByUri.delete(doc.uri.toString());
    }),
    vscode.window.onDidChangeTextEditorSelection((e) => {
      updateLintAtCursor();
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
    vscode.window.onDidChangeActiveTextEditor(() => {
      if (vscode.workspace.getConfiguration("d2v").get("autoOpenPreview", false)) {
        const doc = vscode.window.activeTextEditor?.document;
        if (doc && looksLikeTopology(doc) && !FocusPreviewPanel.current) {
          FocusPreviewPanel.createOrShow(context).refreshFromActiveEditor();
        }
      }
      updateLintAtCursor();
      scheduleUpdate();
    }),
    // lint.enable の切り替えを即座に反映（OFF なら全クリア、ON なら再 lint）
    vscode.workspace.onDidChangeConfiguration((e) => {
      if (e.affectsConfiguration("d2v.lint.enable")) {
        if (vscode.workspace.getConfiguration("d2v").get("lint.enable", true)) {
          vscode.workspace.textDocuments.forEach(lint);
        } else {
          lintCollection.clear();
          lintIndexByUri.clear();
          updateLintAtCursor();
        }
      }
    })
  );

  // YANG スキーマ読み込みと列挙値・キー補完プロバイダの登録
  const schemaRef: { schema: YangSchema | null } = { schema: null };
  const wsFolder = vscode.workspace.workspaceFolders?.[0];
  if (wsFolder) {
    const yangDir = path.join(wsFolder.uri.fsPath, "yang");
    const tryLoad = () => {
      try {
        schemaRef.schema = loadYangSchema(yangDir);
      } catch {
        /* yang/ が存在しない場合は無視 */
      }
    };
    tryLoad();
    const yangWatcher = vscode.workspace.createFileSystemWatcher(
      new vscode.RelativePattern(wsFolder, "yang/*.yang")
    );
    yangWatcher.onDidChange(tryLoad);
    yangWatcher.onDidCreate(tryLoad);
    yangWatcher.onDidDelete(tryLoad);
    context.subscriptions.push(
      yangWatcher,
      vscode.languages.registerCompletionItemProvider(
        { language: "yaml" },
        buildYangCompletionProvider(schemaRef),
        " ",
        ":"
      )
    );
  }

  // 起動時にアクティブな YAML を一度 lint する
  if (vscode.window.activeTextEditor) {
    lint(vscode.window.activeTextEditor.document);
  }
  // autoOpenPreview: 起動時に既に topology YAML が開いていれば自動でプレビューを開く
  if (vscode.workspace.getConfiguration("d2v").get("autoOpenPreview", false)) {
    const doc = vscode.window.activeTextEditor?.document;
    if (doc && looksLikeTopology(doc)) {
      FocusPreviewPanel.createOrShow(context).refreshFromActiveEditor();
    }
  }
}

export function deactivate(): void {
  FocusPreviewPanel.current?.dispose();
}
