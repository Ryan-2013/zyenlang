'use strict';

const vscode = require('vscode');
const path = require('path');
const { spawn } = require('child_process');
const language = require('./language');

const selector = { language: 'zyen' };
const MAX_INDEX_FILE_BYTES = 2 * 1024 * 1024;
const MAX_DIAGNOSTIC_OUTPUT_BYTES = 1024 * 1024;

function symbolKind(kind) {
  return {
    struct: vscode.SymbolKind.Struct,
    function: vscode.SymbolKind.Function,
    native: vscode.SymbolKind.Function,
    method: vscode.SymbolKind.Method,
    field: vscode.SymbolKind.Field,
    variable: vscode.SymbolKind.Variable,
    parameter: vscode.SymbolKind.Variable,
    import: vscode.SymbolKind.Namespace
  }[kind] || vscode.SymbolKind.Variable;
}

function completionKind(kind) {
  return {
    struct: vscode.CompletionItemKind.Struct,
    function: vscode.CompletionItemKind.Function,
    native: vscode.CompletionItemKind.Function,
    method: vscode.CompletionItemKind.Method,
    field: vscode.CompletionItemKind.Field,
    variable: vscode.CompletionItemKind.Variable,
    parameter: vscode.CompletionItemKind.Variable,
    import: vscode.CompletionItemKind.Module
  }[kind] || vscode.CompletionItemKind.Variable;
}

function symbolRange(symbol) {
  return new vscode.Range(symbol.line, symbol.column, symbol.line, symbol.endColumn);
}

function markdownFor(symbol) {
  const result = new vscode.MarkdownString();
  result.appendCodeblock(symbol.detail || symbol.name, 'zyen');
  if (symbol.documentation) result.appendMarkdown(`\n${symbol.documentation}`);
  if (symbol.path) result.appendMarkdown(`\nModule: \`${symbol.path}\``);
  return result;
}

function importSuffix(importPath) {
  const normalized = importPath.replace(/\\/g, '/');
  return `/${normalized.endsWith('.zy') ? normalized : `${normalized}.zy`}`;
}

class WorkspaceIndex {
  constructor() {
    this.documents = new Map();
    this.workspaceLoaded = false;
  }

  parse(document) {
    const key = document.uri.toString();
    const cached = this.documents.get(key);
    if (cached && cached.version === document.version) return cached.parsed;
    const parsed = language.parseDocument(document.getText(), key);
    this.documents.set(key, { version: document.version, parsed, uri: document.uri });
    return parsed;
  }

  forget(uri) {
    this.documents.delete(uri.toString());
  }

  async ensureWorkspace() {
    if (this.workspaceLoaded) return;
    this.workspaceLoaded = true;
    const files = await vscode.workspace.findFiles('**/*.zy', '**/{.git,node_modules,build,dist}/**', 2000);
    await Promise.all(files.map(async (uri) => {
      const key = uri.toString();
      if (this.documents.has(key)) return;
      try {
        const stat = await vscode.workspace.fs.stat(uri);
        if (stat.size > MAX_INDEX_FILE_BYTES) return;
        const bytes = await vscode.workspace.fs.readFile(uri);
        const parsed = language.parseDocument(Buffer.from(bytes).toString('utf8'), key);
        this.documents.set(key, { version: -1, parsed, uri });
      } catch (_) {
        // A file can disappear between findFiles and readFile.
      }
    }));
  }

  invalidateWorkspace() {
    this.workspaceLoaded = false;
    for (const [key, item] of this.documents) {
      if (item.version === -1) this.documents.delete(key);
    }
  }

  all() {
    return [...this.documents.values()];
  }

  symbolsNamed(name) {
    const found = [];
    for (const item of this.documents.values()) {
      for (const symbol of item.parsed.symbols) {
        if (symbol.name === name) found.push({ symbol, uri: item.uri });
      }
    }
    return found;
  }

  membersOf(typeName) {
    const canonicalType = typeName.split('.').pop().trim();
    const found = [];
    for (const item of this.documents.values()) {
      for (const symbol of item.parsed.exports) {
        if (symbol.container === canonicalType || symbol.receiverType === canonicalType) {
          found.push({ symbol, uri: item.uri });
        }
      }
    }
    return found;
  }
}

function completionFromSymbol(symbol) {
  const item = new vscode.CompletionItem(symbol.name, completionKind(symbol.kind));
  item.detail = symbol.detail;
  item.documentation = markdownFor(symbol);
  if (['function', 'native', 'method'].includes(symbol.kind)) {
    item.insertText = new vscode.SnippetString(`${symbol.name}($0)`);
    item.command = { command: 'editor.action.triggerParameterHints', title: 'Parameter hints' };
  }
  return item;
}

function builtInCompletions(moduleName) {
  return (language.BUILTINS[moduleName] || []).map(([name, detail]) => {
    const item = new vscode.CompletionItem(name, vscode.CompletionItemKind.Function);
    item.detail = detail;
    item.documentation = new vscode.MarkdownString().appendCodeblock(detail, 'zyen');
    item.insertText = new vscode.SnippetString(`${name}($0)`);
    return item;
  });
}

function registerLanguageFeatures(context, index) {
  context.subscriptions.push(vscode.languages.registerCompletionItemProvider(selector, {
    async provideCompletionItems(document, position) {
      const parsed = index.parse(document);
      await index.ensureWorkspace();
      const line = document.lineAt(position.line).text;
      const qualified = language.qualifierAt(line, position.character);
      if (qualified) {
        const imported = parsed.imports.find((item) => item.name === qualified.qualifier);
        if (imported) {
          const moduleName = imported.path.split('/').pop();
          const items = builtInCompletions(moduleName);
          for (const source of index.all()) {
            if (!source.parsed.uri.replace(/\\/g, '/').endsWith(importSuffix(imported.path))) continue;
            items.push(...source.parsed.exports.filter((item) => item.visibility !== 'private').map(completionFromSymbol));
          }
          return items;
        }
        const variable = [...parsed.symbols].reverse().find((item) => item.name === qualified.qualifier && item.type);
        if (variable) return index.membersOf(variable.type).map((item) => completionFromSymbol(item.symbol));
      }

      const items = [];
      for (const keyword of language.KEYWORDS) {
        const item = new vscode.CompletionItem(keyword, vscode.CompletionItemKind.Keyword);
        item.detail = 'ZyenLang keyword';
        items.push(item);
      }
      for (const type of language.TYPES) {
        const item = new vscode.CompletionItem(type, vscode.CompletionItemKind.TypeParameter);
        item.detail = 'ZyenLang type';
        items.push(item);
      }
      for (const value of language.SPECIAL_VALUES) {
        items.push(new vscode.CompletionItem(value, vscode.CompletionItemKind.Constant));
      }
      const seen = new Set();
      for (const source of [
        ...parsed.symbols.map((symbol) => ({ symbol })),
        ...index.all().flatMap((item) => item.parsed.exports.map((symbol) => ({ symbol })))
      ]) {
        const key = `${source.symbol.kind}:${source.symbol.name}:${source.symbol.container || ''}`;
        if (seen.has(key)) continue;
        seen.add(key);
        items.push(completionFromSymbol(source.symbol));
      }
      return items;
    }
  }, '.'));

  context.subscriptions.push(vscode.languages.registerHoverProvider(selector, {
    async provideHover(document, position) {
      const word = language.wordAt(document.lineAt(position.line).text, position.character);
      if (!word) return undefined;
      index.parse(document);
      await index.ensureWorkspace();
      const local = index.symbolsNamed(word.value);
      if (local.length) return new vscode.Hover(markdownFor(local[0].symbol));
      for (const values of Object.values(language.BUILTINS)) {
        const entry = values.find(([name]) => name === word.value);
        if (entry) return new vscode.Hover(new vscode.MarkdownString().appendCodeblock(entry[1], 'zyen'));
      }
      if (language.KEYWORDS.includes(word.value)) return new vscode.Hover(`ZyenLang keyword \`${word.value}\``);
      if (language.TYPES.includes(word.value)) return new vscode.Hover(`ZyenLang type \`${word.value}\``);
      return undefined;
    }
  }));

  context.subscriptions.push(vscode.languages.registerDefinitionProvider(selector, {
    async provideDefinition(document, position) {
      const word = language.wordAt(document.lineAt(position.line).text, position.character);
      if (!word) return undefined;
      const parsed = index.parse(document);
      await index.ensureWorkspace();
      const imported = parsed.imports.find((item) => item.name === word.value);
      if (imported) {
        const suffix = importSuffix(imported.path);
        const module = index.all().find((item) => item.parsed.uri.replace(/\\/g, '/').endsWith(suffix));
        if (module) return new vscode.Location(module.uri, new vscode.Position(0, 0));
      }
      const found = index.symbolsNamed(word.value);
      if (!found.length) return undefined;
      const sameDocument = found.find((item) => item.uri.toString() === document.uri.toString());
      const target = sameDocument || found[0];
      return new vscode.Location(target.uri, symbolRange(target.symbol));
    }
  }));

  context.subscriptions.push(vscode.languages.registerDocumentSymbolProvider(selector, {
    provideDocumentSymbols(document) {
      const parsed = index.parse(document);
      const parents = new Map();
      const result = [];
      for (const symbol of parsed.exports) {
        const range = document.lineAt(symbol.line).range;
        const selection = symbolRange(symbol);
        const entry = new vscode.DocumentSymbol(symbol.name, symbol.detail || '', symbolKind(symbol.kind), range, selection);
        if (symbol.kind === 'struct') {
          parents.set(symbol.name, entry);
          result.push(entry);
        } else if (symbol.container && parents.has(symbol.container)) {
          parents.get(symbol.container).children.push(entry);
        } else {
          result.push(entry);
        }
      }
      return result;
    }
  }));

  context.subscriptions.push(vscode.languages.registerWorkspaceSymbolProvider({
    async provideWorkspaceSymbols(query) {
      await index.ensureWorkspace();
      const needle = query.toLowerCase();
      const result = [];
      for (const item of index.all()) {
        for (const symbol of item.parsed.exports) {
          if (needle && !symbol.name.toLowerCase().includes(needle)) continue;
          result.push(new vscode.SymbolInformation(
            symbol.name,
            symbolKind(symbol.kind),
            symbol.container || '',
            new vscode.Location(item.uri, symbolRange(symbol))
          ));
        }
      }
      return result;
    }
  }));

  context.subscriptions.push(vscode.languages.registerSignatureHelpProvider(selector, {
    async provideSignatureHelp(document, position) {
      const call = language.callAt(document.lineAt(position.line).text, position.character);
      if (!call) return undefined;
      index.parse(document);
      await index.ensureWorkspace();
      const symbols = index.symbolsNamed(call.name).filter((item) => ['function', 'method', 'native'].includes(item.symbol.kind));
      let detail = symbols.length ? symbols[0].symbol.detail : undefined;
      if (!detail) {
        for (const values of Object.values(language.BUILTINS)) {
          const found = values.find(([name]) => name === call.name);
          if (found) { detail = found[1]; break; }
        }
      }
      if (!detail) return undefined;
      const help = new vscode.SignatureHelp();
      const signature = new vscode.SignatureInformation(detail);
      const args = detail.match(/\((.*)\)/);
      signature.parameters = args ? language.splitTopLevel(args[1]).map((value) => new vscode.ParameterInformation(value)) : [];
      help.signatures = [signature];
      help.activeSignature = 0;
      help.activeParameter = Math.min(call.activeParameter, Math.max(0, signature.parameters.length - 1));
      return help;
    }
  }, '(', ','));

  context.subscriptions.push(vscode.languages.registerReferenceProvider(selector, {
    async provideReferences(document, position, options) {
      const word = language.wordAt(document.lineAt(position.line).text, position.character);
      if (!word) return [];
      index.parse(document);
      await index.ensureWorkspace();
      const references = [];
      const escaped = word.value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      const pattern = new RegExp(`\\b${escaped}\\b`, 'g');
      for (const item of index.all()) {
        for (let lineNumber = 0; lineNumber < item.parsed.lines.length; lineNumber += 1) {
          const line = item.parsed.lines[lineNumber];
          let match;
          while ((match = pattern.exec(line)) !== null) {
            const isDeclaration = item.parsed.symbols.some((symbol) => symbol.name === word.value && symbol.line === lineNumber && symbol.column === match.index);
            if (options.includeDeclaration || !isDeclaration) {
              references.push(new vscode.Location(item.uri, new vscode.Range(lineNumber, match.index, lineNumber, match.index + word.value.length)));
            }
          }
        }
      }
      return references;
    }
  }));
}

class DiagnosticsController {
  constructor(context, index) {
    this.index = index;
    this.collection = vscode.languages.createDiagnosticCollection('zyenlang');
    this.timers = new Map();
    this.running = new Map();
    this.generations = new Map();
    this.targets = new Map();
    this.status = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 30);
    this.status.command = 'zyenlang.check';
    this.status.text = '$(check) ZyenLang';
    this.status.tooltip = 'Check current ZyenLang file';
    context.subscriptions.push(this.collection, this.status);
    if (vscode.window.activeTextEditor?.document.languageId === 'zyen') this.status.show();
  }

  schedule(document, immediate = false) {
    if (document.languageId !== 'zyen' || document.uri.scheme !== 'file') return;
    if (!vscode.workspace.isTrusted) {
      this.status.text = '$(shield) ZyenLang';
      this.status.tooltip = 'Trust this workspace to enable compiler diagnostics.';
      return;
    }
    if (!vscode.workspace.getConfiguration('zyenlang', document.uri).get('diagnostics.enable', true)) return;
    const key = document.uri.toString();
    clearTimeout(this.timers.get(key));
    const delay = immediate ? 0 : vscode.workspace.getConfiguration('zyenlang', document.uri).get('diagnostics.delay', 300);
    this.timers.set(key, setTimeout(() => this.check(document), delay));
  }

  check(document) {
    const key = document.uri.toString();
    const old = this.running.get(key);
    if (old) old.kill();
    const generation = (this.generations.get(key) || 0) + 1;
    this.generations.set(key, generation);
    const config = vscode.workspace.getConfiguration('zyenlang', document.uri);
    const source = document.getText();
    const maxFileBytes = config.get('diagnostics.maxFileSizeKb', 1024) * 1024;
    if (Buffer.byteLength(source, 'utf8') > maxFileBytes) {
      const warning = new vscode.Diagnostic(
        new vscode.Range(0, 0, 0, Math.max(1, document.lineAt(0).text.length)),
        `Live check skipped: file exceeds the ${Math.floor(maxFileBytes / 1024)} KiB safety limit.`,
        vscode.DiagnosticSeverity.Warning
      );
      warning.source = 'zy2';
      this.collection.set(document.uri, [warning]);
      this.status.text = '$(warning) ZyenLang';
      return;
    }
    const executable = config.get('compilerPath', 'zy2');
    const cwd = vscode.workspace.getWorkspaceFolder(document.uri)?.uri.fsPath || path.dirname(document.uri.fsPath);
    const child = spawn(executable, ['check', document.uri.fsPath, '--library', '--stdin'], {
      cwd,
      windowsHide: true,
      shell: false
    });
    this.running.set(key, child);
    let stdout = '';
    let stderr = '';
    let spawnFailed = false;
    let failureReason = '';
    const timeoutMs = config.get('diagnostics.timeout', 15000);
    const timer = setTimeout(() => {
      failureReason = `Compiler check exceeded ${timeoutMs} ms and was stopped.`;
      child.kill();
    }, timeoutMs);
    const appendOutput = (target, chunk) => {
      const combined = target + chunk.toString();
      if (Buffer.byteLength(combined, 'utf8') > MAX_DIAGNOSTIC_OUTPUT_BYTES) {
        failureReason = 'Compiler output exceeded the 1 MiB safety limit and was stopped.';
        child.kill();
        return target;
      }
      return combined;
    };
    child.stdout.on('data', (chunk) => { stdout = appendOutput(stdout, chunk); });
    child.stderr.on('data', (chunk) => { stderr = appendOutput(stderr, chunk); });
    child.on('error', (error) => {
      if (this.generations.get(key) !== generation) return;
      spawnFailed = true;
      const range = new vscode.Range(0, 0, 0, Math.max(1, document.lineAt(0).text.length));
      this.collection.set(document.uri, [new vscode.Diagnostic(range, `Cannot start ${executable}: ${error.message}`, vscode.DiagnosticSeverity.Error)]);
      this.status.text = '$(error) ZyenLang compiler';
      this.status.tooltip = `Set zyenlang.compilerPath to the zy2 executable.\n${error.message}`;
    });
    child.on('close', async (code) => {
      clearTimeout(timer);
      if (this.generations.get(key) !== generation) return;
      this.running.delete(key);
      if (spawnFailed) return;
      if (failureReason) {
        const diagnostic = new vscode.Diagnostic(new vscode.Range(0, 0, 0, 1), failureReason, vscode.DiagnosticSeverity.Error);
        diagnostic.source = 'zy2';
        this.collection.set(document.uri, [diagnostic]);
        this.status.text = '$(error) ZyenLang';
        this.status.tooltip = failureReason;
        return;
      }
      if (code === null) return;
      const groups = await this.parseDiagnostics(document, `${stderr}\n${stdout}`, cwd);
      const previous = this.targets.get(key) || new Set();
      const next = new Set(groups.keys());
      for (const oldUri of previous) {
        if (!next.has(oldUri)) this.collection.delete(vscode.Uri.parse(oldUri));
      }
      for (const [uriString, diagnostics] of groups) {
        this.collection.set(vscode.Uri.parse(uriString), diagnostics);
      }
      this.targets.set(key, next);
      const count = [...groups.values()].reduce((total, values) => total + values.length, 0);
      this.status.text = count ? `$(error) ZyenLang ${count}` : '$(check) ZyenLang';
      this.status.tooltip = count ? `${count} compiler diagnostic(s)` : 'No compiler errors';
    });
    child.stdin.on('error', () => {});
    child.stdin.end(source);
  }

  async parseDiagnostics(document, output, cwd) {
    const groups = new Map();
    const seen = new Set();
    for (const raw of output.split(/\r?\n/)) {
      const match = raw.match(/^(.+?):(\d+):(\d+):\s*(.+)$/);
      if (!match) continue;
      const sourceName = match[1].trim();
      let uri = document.uri;
      if (!sourceName.startsWith('<')) {
        const sourcePath = path.isAbsolute(sourceName) ? sourceName : path.resolve(cwd, sourceName);
        uri = vscode.Uri.file(sourcePath);
      }
      const line = Math.max(0, Number(match[2]) - 1);
      const column = Math.max(0, Number(match[3]) - 1);
      let end = column + 1;
      if (uri.toString() === document.uri.toString() && line < document.lineCount) {
        const text = document.lineAt(line).text;
        const safeColumn = Math.min(text.length, column);
        const word = document.getWordRangeAtPosition(new vscode.Position(line, safeColumn));
        end = Math.max(safeColumn + 1, word?.end.character || text.length);
      }
      const signature = `${uri}:${line}:${column}:${match[4]}`;
      if (seen.has(signature)) continue;
      seen.add(signature);
      const diagnostic = new vscode.Diagnostic(new vscode.Range(line, column, line, end), match[4], vscode.DiagnosticSeverity.Error);
      diagnostic.source = 'zy2';
      const uriString = uri.toString();
      if (!groups.has(uriString)) groups.set(uriString, []);
      groups.get(uriString).push(diagnostic);
    }
    if (!groups.size && output.trim() && !/^OK:/m.test(output)) {
      const diagnostic = new vscode.Diagnostic(new vscode.Range(0, 0, 0, Math.max(1, document.lineAt(0).text.length)), output.trim(), vscode.DiagnosticSeverity.Error);
      diagnostic.source = 'zy2';
      groups.set(document.uri.toString(), [diagnostic]);
    }
    if (!groups.size) groups.set(document.uri.toString(), []);
    return groups;
  }

  disposeDocument(uri) {
    const key = uri.toString();
    clearTimeout(this.timers.get(key));
    this.running.get(key)?.kill();
    for (const target of this.targets.get(key) || []) this.collection.delete(vscode.Uri.parse(target));
    this.targets.delete(key);
    this.collection.delete(uri);
  }
}

async function saveCurrentDocument() {
  const editor = vscode.window.activeTextEditor;
  if (!editor || editor.document.languageId !== 'zyen') {
    vscode.window.showWarningMessage('Open a ZyenLang .zy file first.');
    return undefined;
  }
  if (editor.document.isUntitled) {
    vscode.window.showWarningMessage('Save the ZyenLang file before running or building it.');
    return undefined;
  }
  if (editor.document.isDirty && !(await editor.document.save())) return undefined;
  return editor.document;
}

async function requireTrustedWorkspace() {
  if (vscode.workspace.isTrusted) return true;
  const trusted = await vscode.workspace.requestWorkspaceTrust();
  if (!trusted) vscode.window.showWarningMessage('ZyenLang compiler commands require a trusted workspace.');
  return trusted;
}

async function processTask(document, args, label) {
  const config = vscode.workspace.getConfiguration('zyenlang', document.uri);
  const executable = config.get('compilerPath', 'zy2');
  const cwd = vscode.workspace.getWorkspaceFolder(document.uri)?.uri.fsPath || path.dirname(document.uri.fsPath);
  const task = new vscode.Task(
    { type: 'zyenlang', command: args[0] },
    vscode.TaskScope.Workspace,
    label,
    'ZyenLang',
    new vscode.ProcessExecution(executable, args, { cwd })
  );
  task.presentationOptions = { reveal: vscode.TaskRevealKind.Always, panel: vscode.TaskPanelKind.Shared, clear: false };
  await vscode.tasks.executeTask(task);
}

function registerCommands(context, diagnostics) {
  context.subscriptions.push(vscode.commands.registerCommand('zyenlang.check', () => {
    const document = vscode.window.activeTextEditor?.document;
    if (document?.languageId === 'zyen') diagnostics.schedule(document, true);
  }));

  context.subscriptions.push(vscode.commands.registerCommand('zyenlang.run', async () => {
    if (!(await requireTrustedWorkspace())) return;
    const document = await saveCurrentDocument();
    if (!document) return;
    const args = ['run', document.uri.fsPath];
    if (vscode.workspace.getConfiguration('zyenlang', document.uri).get('build.release', true)) args.push('--release');
    await processTask(document, args, 'Run current file');
  }));

  context.subscriptions.push(vscode.commands.registerCommand('zyenlang.build', async () => {
    if (!(await requireTrustedWorkspace())) return;
    const document = await saveCurrentDocument();
    if (!document) return;
    const extension = process.platform === 'win32' ? '.exe' : '';
    const target = await vscode.window.showSaveDialog({
      defaultUri: vscode.Uri.file(path.join(path.dirname(document.uri.fsPath), 'build', `${path.basename(document.uri.fsPath, '.zy')}${extension}`)),
      saveLabel: 'Build'
    });
    if (!target) return;
    const args = ['build', document.uri.fsPath, '-o', target.fsPath];
    if (vscode.workspace.getConfiguration('zyenlang', document.uri).get('build.release', true)) args.push('--release');
    await processTask(document, args, 'Build executable');
  }));

  context.subscriptions.push(vscode.commands.registerCommand('zyenlang.emitC', async () => {
    if (!(await requireTrustedWorkspace())) return;
    const document = await saveCurrentDocument();
    if (!document) return;
    const target = await vscode.window.showSaveDialog({
      defaultUri: vscode.Uri.file(path.join(path.dirname(document.uri.fsPath), 'build', `${path.basename(document.uri.fsPath, '.zy')}.c`)),
      filters: { 'C source': ['c'] },
      saveLabel: 'Emit C'
    });
    if (!target) return;
    await processTask(document, ['build', document.uri.fsPath, '-o', target.fsPath], 'Emit C source');
  }));
}

function activate(context) {
  const index = new WorkspaceIndex();
  const diagnostics = new DiagnosticsController(context, index);
  registerLanguageFeatures(context, index);
  registerCommands(context, diagnostics);

  context.subscriptions.push(vscode.workspace.onDidOpenTextDocument((document) => {
    if (document.languageId === 'zyen') {
      index.parse(document);
      diagnostics.schedule(document, true);
    }
  }));
  context.subscriptions.push(vscode.workspace.onDidChangeTextDocument((event) => {
    if (event.document.languageId === 'zyen') {
      index.parse(event.document);
      diagnostics.schedule(event.document);
    }
  }));
  context.subscriptions.push(vscode.workspace.onDidSaveTextDocument((document) => {
    if (document.languageId === 'zyen') diagnostics.schedule(document, true);
  }));
  context.subscriptions.push(vscode.workspace.onDidCloseTextDocument((document) => {
    index.forget(document.uri);
    diagnostics.disposeDocument(document.uri);
  }));
  context.subscriptions.push(vscode.window.onDidChangeActiveTextEditor((editor) => {
    if (editor?.document.languageId === 'zyen') diagnostics.status.show();
    else diagnostics.status.hide();
  }));
  context.subscriptions.push(vscode.workspace.onDidGrantWorkspaceTrust(() => {
    for (const document of vscode.workspace.textDocuments) {
      if (document.languageId === 'zyen') diagnostics.schedule(document, true);
    }
  }));
  const watcher = vscode.workspace.createFileSystemWatcher('**/*.zy');
  watcher.onDidCreate(() => index.invalidateWorkspace());
  watcher.onDidChange((uri) => { index.forget(uri); index.invalidateWorkspace(); });
  watcher.onDidDelete((uri) => { index.forget(uri); index.invalidateWorkspace(); });
  context.subscriptions.push(watcher);

  for (const document of vscode.workspace.textDocuments) {
    if (document.languageId === 'zyen') {
      index.parse(document);
      diagnostics.schedule(document, true);
    }
  }
}

function deactivate() {}

module.exports = { activate, deactivate };
