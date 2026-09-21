'use strict';

const vscode = require('vscode');
const path = require('path');
const { spawn } = require('child_process');
const language = require('./language');
const projectTools = require('./project');

const selector = { language: 'zyen' };
const MAX_INDEX_FILE_BYTES = 2 * 1024 * 1024;
const MAX_DIAGNOSTIC_OUTPUT_BYTES = 1024 * 1024;

function symbolKind(kind) {
  return {
    struct: vscode.SymbolKind.Struct,
    class: vscode.SymbolKind.Class,
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
    class: vscode.CompletionItemKind.Class,
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

  projectFor(document) {
    return projectTools.loadProject(document.uri.fsPath);
  }

  async loadFile(filePath) {
    if (!filePath) return undefined;
    const uri = vscode.Uri.file(filePath);
    const key = uri.toString();
    if (this.documents.has(key)) return this.documents.get(key);
    try {
      const stat = await vscode.workspace.fs.stat(uri);
      if (stat.size > MAX_INDEX_FILE_BYTES) return undefined;
      const bytes = await vscode.workspace.fs.readFile(uri);
      const parsed = language.parseDocument(Buffer.from(bytes).toString('utf8'), key);
      const item = { version: -1, parsed, uri };
      this.documents.set(key, item);
      return item;
    } catch (_) {
      return undefined;
    }
  }

  async moduleFor(document, importPath) {
    const currentProject = this.projectFor(document);
    const exact = projectTools.importFile(importPath, document.uri.fsPath, currentProject);
    if (exact) return this.loadFile(exact);
    if (importPath.startsWith('std::')) {
      const suffix = `/std/${importPath.slice(5).replace(/::/g, '/')}.zy`;
      return this.all().find((item) => item.uri.fsPath.replace(/\\/g, '/').endsWith(suffix));
    }
    return undefined;
  }

  async moduleExports(document, imported) {
    const result = [];
    const moduleName = imported.path.split('::').pop();
    for (const [name, detail] of language.BUILTINS[moduleName] || []) {
      result.push({ symbol: { name, kind: 'function', detail, returnType: language.returnTypeFromDetail(detail), visibility: 'public', builtin: true } });
    }
    for (const [name, detail] of language.BUILTIN_TYPES[moduleName] || []) {
      result.push({ symbol: { name, kind: 'class', detail, visibility: 'public', builtin: true } });
    }
    if (moduleName === 'c_module') {
      result.push({ symbol: { name: 'load', kind: 'function', detail: 'fn load(path: str) c_module::Module', returnType: 'c_module::Module', visibility: 'public', builtin: true } });
    }
    const module = await this.moduleFor(document, imported.path);
    if (module) {
      result.push(...module.parsed.exports
        .filter((symbol) => symbol.visibility !== 'private')
        .map((symbol) => ({ symbol, uri: module.uri })));
    }
    return result;
  }

  async membersOf(typeName, document) {
    const normalized = language.normalizeType(typeName);
    const pathParts = normalized.split('::');
    const canonicalType = language.baseType(pathParts.pop());
    const found = [];
    for (const symbol of language.BUILTIN_MEMBERS[canonicalType] || []) {
      found.push({ symbol: { ...symbol, visibility: 'public', builtin: true } });
    }
    let sources = this.all();
    if (pathParts.length && document) {
      const parsed = this.parse(document);
      const imported = parsed.imports.find((item) => item.name === pathParts[0]);
      const module = imported ? await this.moduleFor(document, imported.path) : undefined;
      if (module) sources = [module];
    } else if (document) {
      const current = this.documents.get(document.uri.toString());
      const ownType = current?.parsed.symbols.some((item) => item.name === canonicalType && ['class', 'struct'].includes(item.kind));
      if (ownType) sources = [current];
    }
    for (const item of sources) {
      for (const symbol of item.parsed.exports) {
        if (symbol.container === canonicalType || symbol.receiverType === canonicalType) {
          found.push({ symbol, uri: item.uri });
        }
      }
    }
    return [...new Map(found.map((item) => [item.symbol.name, item])).values()];
  }

  visibleLocal(parsed, name, line) {
    const active = parsed.functions.find((item) => item.startLine <= line && line <= item.endLine);
    return [...parsed.symbols].reverse().find((symbol) =>
      symbol.name === name && symbol.line <= line &&
      (!['variable', 'parameter'].includes(symbol.kind) || !symbol.container || symbol.container === active?.name)
    );
  }

  async typeOfPath(document, accessPath, line) {
    const parsed = this.parse(document);
    const segments = accessPath.split('.');
    const root = segments.shift();
    let typeName;
    if (root === 'this') {
      const fn = activeFunction(parsed, line);
      typeName = parsed.exports.find((item) => item.kind === 'method' && item.name === fn?.name)?.container;
    } else {
      typeName = this.visibleLocal(parsed, root, line)?.type;
    }
    for (const segment of segments) {
      if (!typeName) return '';
      const member = (await this.membersOf(typeName, document)).find((item) => item.symbol.name === segment && !item.symbol.static)?.symbol;
      typeName = member?.type || member?.returnType || language.returnTypeFromDetail(member?.detail);
    }
    return typeName || '';
  }

  async resolve(document, position) {
    const lineText = document.lineAt(position.line).text;
    const word = language.wordAt(lineText, position.character);
    if (!word) return undefined;
    const parsed = this.parse(document);
    const prefix = lineText.slice(0, word.start);
    const owner = prefix.match(/([A-Za-z_]\w*(?:(?:::|\.)[A-Za-z_]\w*)*)(::|\.)\s*$/);
    if (owner) {
      const ownerPath = owner[1];
      if (owner[2] === '::') {
        const segments = ownerPath.split('::');
        const imported = parsed.imports.find((item) => item.name === segments[0]);
        if (imported) {
          if (segments.length === 1) {
            return (await this.moduleExports(document, imported)).find((item) => item.symbol.name === word.value);
          }
          const members = await this.membersOf(`${segments[0]}::${segments[1]}`, document);
          return members.find((item) => item.symbol.name === word.value && item.symbol.static);
        }
        const members = await this.membersOf(ownerPath, document);
        return members.find((item) => item.symbol.name === word.value && item.symbol.static);
      }
      const typeName = await this.typeOfPath(document, ownerPath, position.line);
      if (typeName) {
        const members = await this.membersOf(typeName, document);
        return members.find((item) => item.symbol.name === word.value && !item.symbol.static);
      }
    }
    const imported = parsed.imports.find((item) => item.name === word.value);
    if (imported) return { symbol: imported, module: await this.moduleFor(document, imported.path), uri: document.uri };
    const local = this.visibleLocal(parsed, word.value, position.line);
    return local ? { symbol: local, uri: document.uri } : undefined;
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

function specialFormCompletion(form) {
  const item = new vscode.CompletionItem(form.name, vscode.CompletionItemKind.Function);
  item.detail = form.detail;
  item.documentation = new vscode.MarkdownString(form.documentation).appendCodeblock(form.detail, 'zyen');
  item.insertText = new vscode.SnippetString(form.snippet);
  item.command = { command: 'editor.action.triggerParameterHints', title: 'Parameter hints' };
  return item;
}

function wordLocations(item, name) {
  const result = [];
  const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const pattern = new RegExp(`\\b${escaped}\\b`, 'g');
  for (let lineNumber = 0; lineNumber < item.parsed.maskedLines.length; lineNumber += 1) {
    const line = item.parsed.maskedLines[lineNumber];
    let match;
    while ((match = pattern.exec(line)) !== null) {
      result.push(new vscode.Location(
        item.uri,
        new vscode.Range(lineNumber, match.index, lineNumber, match.index + name.length)
      ));
    }
  }
  return result;
}

function dedupeCompletions(items) {
  const seen = new Set();
  return items.filter((item) => {
    const key = `${item.label}:${item.kind}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function activeFunction(parsed, line) {
  return parsed.functions.find((item) => item.startLine <= line && line <= item.endLine);
}

function moduleCompletion(pathValue, prefix) {
  const candidate = pathValue.split('::').slice(1);
  const input = prefix.split('::');
  const typed = input.pop() || '';
  if (input.some((part, index) => candidate[index] !== part)) return undefined;
  const label = candidate[input.length];
  if (!label) return undefined;
  if (!label.startsWith(typed)) return undefined;
  const item = new vscode.CompletionItem(label, vscode.CompletionItemKind.Module);
  item.detail = pathValue;
  item.insertText = label;
  return item;
}

function registerLanguageFeatures(context, index) {
  context.subscriptions.push(vscode.languages.registerCompletionItemProvider(selector, {
    async provideCompletionItems(document, position) {
      const parsed = index.parse(document);
      await index.ensureWorkspace();
      const line = document.lineAt(position.line).text;
      const importRoot = language.importRootAt(line, position.character);
      if (importRoot) {
        const currentProject = index.projectFor(document);
        const roots = ['std', 'crate', ...[...(currentProject?.dependencies.keys() || [])]];
        return roots.filter((item) => item.startsWith(importRoot.prefix)).map((name) => {
          const item = new vscode.CompletionItem(name, vscode.CompletionItemKind.Module);
          item.insertText = `${name}::`;
          item.command = { command: 'editor.action.triggerSuggest', title: 'Continue module path' };
          return item;
        });
      }
      const importPath = language.importPathAt(line, position.character);
      if (importPath) {
        const candidates = [];
        if (importPath.kind === 'std') {
          candidates.push(...language.STANDARD_MODULES.map((name) => `std::${name}`));
          for (const source of index.all()) {
            const match = source.uri.fsPath.replace(/\\/g, '/').match(/\/std\/(.+)\.zy$/);
            if (match) candidates.push(`std::${match[1].replace(/\//g, '::')}`);
          }
        }
        const currentProject = index.projectFor(document);
        if (importPath.kind === 'crate') candidates.push(...projectTools.discoverCrateModules(currentProject));
        if (!['std', 'crate'].includes(importPath.kind) && currentProject?.dependencies.has(importPath.kind)) {
          const dependency = currentProject.dependencies.get(importPath.kind);
          if (dependency.kind === 'path') {
            const dependencyProject = projectTools.loadProject(path.resolve(currentProject.root, dependency.path));
            candidates.push(...projectTools.discoverCrateModules(dependencyProject).map((item) => item.replace(/^crate/, importPath.kind)));
          }
        }
        return dedupeCompletions(candidates.map((item) => moduleCompletion(item, importPath.prefix)).filter(Boolean));
      }
      const qualified = language.accessPathAt(line, position.character);
      if (qualified) {
        const root = qualified.path.split(/::|\./)[0];
        const imported = parsed.imports.find((item) => item.name === root);
        if (imported && qualified.separator === '::') {
          if (!qualified.path.includes('::')) {
            return dedupeCompletions((await index.moduleExports(document, imported)).map((item) => completionFromSymbol(item.symbol)));
          }
          return (await index.membersOf(qualified.path, document))
            .filter((item) => item.symbol.static)
            .map((item) => completionFromSymbol(item.symbol));
        }
        if (qualified.separator === '.') {
          const typeName = await index.typeOfPath(document, qualified.path, position.line);
          if (typeName) return (await index.membersOf(typeName, document))
            .filter((item) => !item.symbol.static)
            .map((item) => completionFromSymbol(item.symbol));
        } else {
          const type = parsed.symbols.find((item) => ['class', 'struct'].includes(item.kind) && item.name === qualified.qualifier);
          if (type) return (await index.membersOf(type.name, document))
            .filter((item) => item.symbol.static)
            .map((item) => completionFromSymbol(item.symbol));
        }
      }

      const items = [];
      const specialNames = new Set(language.SPECIAL_FORMS.map((form) => form.name));
      for (const keyword of language.KEYWORDS) {
        if (specialNames.has(keyword)) continue;
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
      items.push(...language.SPECIAL_FORMS.map(specialFormCompletion));
      const fn = activeFunction(parsed, position.line);
      const visible = parsed.symbols.filter((symbol) => {
        if (!['variable', 'parameter'].includes(symbol.kind)) return !symbol.container || ['class', 'struct'].includes(symbol.kind);
        return symbol.line <= position.line && (!symbol.container || symbol.container === fn?.name);
      });
      items.push(...visible.map(completionFromSymbol));
      items.push(...parsed.imports.map(completionFromSymbol));
      return dedupeCompletions(items);
    }
  }, '.', ':'));

  context.subscriptions.push(vscode.languages.registerHoverProvider(selector, {
    async provideHover(document, position) {
      const word = language.wordAt(document.lineAt(position.line).text, position.character);
      if (!word) return undefined;
      index.parse(document);
      await index.ensureWorkspace();
      const resolved = await index.resolve(document, position);
      if (resolved) return new vscode.Hover(markdownFor(resolved.symbol));
      const special = language.SPECIAL_FORMS.find((item) => item.name === word.value);
      if (special) {
        return new vscode.Hover(new vscode.MarkdownString(special.documentation).appendCodeblock(special.detail, 'zyen'));
      }
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
      index.parse(document);
      await index.ensureWorkspace();
      const resolved = await index.resolve(document, position);
      if (!resolved) return undefined;
      if (resolved.module) return new vscode.Location(resolved.module.uri, new vscode.Position(0, 0));
      if (!resolved.uri || resolved.symbol.builtin) return undefined;
      return new vscode.Location(resolved.uri, symbolRange(resolved.symbol));
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
        if (['struct', 'class'].includes(symbol.kind)) {
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
      const call = language.callPathAt(document.lineAt(position.line).text, position.character);
      if (!call) return undefined;
      const parsed = index.parse(document);
      await index.ensureWorkspace();
      let detail;
      if (call.path.includes('::')) {
        const parts = call.path.split('::');
        const imported = parsed.imports.find((item) => item.name === parts[0]);
        if (imported && parts.length === 2) detail = (await index.moduleExports(document, imported)).find((item) => item.symbol.name === call.name)?.symbol.detail;
        else if (parts.length > 2) detail = (await index.membersOf(parts.slice(0, -1).join('::'), document)).find((item) => item.symbol.name === call.name)?.symbol.detail;
      } else if (call.path.includes('.')) {
        const receiver = call.path.slice(0, call.path.lastIndexOf('.'));
        const typeName = await index.typeOfPath(document, receiver, position.line);
        if (typeName) detail = (await index.membersOf(typeName, document)).find((item) => item.symbol.name === call.name)?.symbol.detail;
      } else {
        detail = index.visibleLocal(parsed, call.name, position.line)?.detail;
      }
      if (!detail) {
        const special = language.SPECIAL_FORMS.find((item) => item.name === call.name);
        if (special) detail = special.detail;
      }
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
      const parsed = index.parse(document);
      const resolved = await index.resolve(document, position);
      if (!resolved) return [];
      const fn = activeFunction(parsed, position.line);
      const locations = wordLocations({ parsed, uri: document.uri }, word.value).filter((location) => {
        if (!['variable', 'parameter'].includes(resolved.symbol.kind) || !fn) return true;
        return fn.startLine <= location.range.start.line && location.range.start.line <= fn.endLine;
      });
      return locations.filter((location) => options.includeDeclaration || !location.range.isEqual(symbolRange(resolved.symbol)));
    }
  }));

  context.subscriptions.push(vscode.languages.registerDocumentHighlightProvider(selector, {
    async provideDocumentHighlights(document, position) {
      const word = language.wordAt(document.lineAt(position.line).text, position.character);
      if (!word) return [];
      const parsed = index.parse(document);
      const resolved = await index.resolve(document, position);
      const item = { parsed, uri: document.uri };
      const fn = activeFunction(parsed, position.line);
      return wordLocations(item, word.value).filter((location) => {
        if (!resolved || !['variable', 'parameter'].includes(resolved.symbol.kind) || !fn) return true;
        return fn.startLine <= location.range.start.line && location.range.start.line <= fn.endLine;
      }).map((location) =>
        new vscode.DocumentHighlight(location.range, vscode.DocumentHighlightKind.Read)
      );
    }
  }));

  context.subscriptions.push(vscode.languages.registerRenameProvider(selector, {
    async prepareRename(document, position) {
      const resolved = await index.resolve(document, position);
      if (!resolved || resolved.uri?.toString() !== document.uri.toString() || resolved.symbol.builtin) {
        throw new Error('Only symbols declared in the current ZyenLang module can be renamed safely.');
      }
      if (!['variable', 'parameter', 'import'].includes(resolved.symbol.kind) && resolved.symbol.visibility !== 'private') {
        throw new Error('Public API rename is disabled because it can affect modules outside the workspace.');
      }
      const range = document.getWordRangeAtPosition(position);
      if (!range) throw new Error('Place the cursor on a ZyenLang identifier.');
      return range;
    },
    async provideRenameEdits(document, position, newName) {
      if (!/^[A-Za-z_]\w*$/.test(newName)) throw new Error('ZyenLang names must be identifiers.');
      const parsed = index.parse(document);
      const resolved = await index.resolve(document, position);
      if (!resolved || resolved.uri?.toString() !== document.uri.toString()) return undefined;
      const fn = activeFunction(parsed, position.line);
      const collision = parsed.symbols.find((item) => item.name === newName && item !== resolved.symbol && (
        !['variable', 'parameter'].includes(resolved.symbol.kind) || item.container === fn?.name
      ));
      if (collision) throw new Error(`The name ${newName} is already declared in this scope.`);
      const edit = new vscode.WorkspaceEdit();
      for (const location of wordLocations({ parsed, uri: document.uri }, resolved.symbol.name)) {
        if (['variable', 'parameter'].includes(resolved.symbol.kind) && fn &&
            (location.range.start.line < fn.startLine || location.range.start.line > fn.endLine)) continue;
        edit.replace(document.uri, location.range, newName);
      }
      return edit;
    }
  }));

  context.subscriptions.push(vscode.languages.registerTypeDefinitionProvider(selector, {
    async provideTypeDefinition(document, position) {
      const resolved = await index.resolve(document, position);
      if (!resolved?.symbol.type) return undefined;
      const typeName = language.baseType(resolved.symbol.type);
      const parsed = index.parse(document);
      const own = parsed.symbols.find((item) => ['struct', 'class'].includes(item.kind) && item.name === typeName);
      if (own) return new vscode.Location(document.uri, symbolRange(own));
      const parts = language.normalizeType(resolved.symbol.type).split('::');
      if (parts.length > 1) {
        const imported = parsed.imports.find((item) => item.name === parts[0]);
        const module = imported ? await index.moduleFor(document, imported.path) : undefined;
        const target = module?.parsed.symbols.find((item) => ['struct', 'class'].includes(item.kind) && item.name === language.baseType(parts.at(-1)));
        if (module && target) return new vscode.Location(module.uri, symbolRange(target));
      }
      for (const imported of parsed.imports) {
        const module = await index.moduleFor(document, imported.path);
        const target = module?.parsed.symbols.find((item) => ['struct', 'class'].includes(item.kind) && item.name === typeName);
        if (module && target) return new vscode.Location(module.uri, symbolRange(target));
      }
      return undefined;
    }
  }));

  context.subscriptions.push(vscode.languages.registerDocumentLinkProvider(selector, {
    async provideDocumentLinks(document) {
      await index.ensureWorkspace();
      const parsed = index.parse(document);
      const links = [];
      for (const imported of parsed.imports) {
        const module = await index.moduleFor(document, imported.path);
        if (!module) continue;
        const line = document.lineAt(imported.line).text;
        const start = line.indexOf(imported.path);
        if (start >= 0) links.push(new vscode.DocumentLink(new vscode.Range(imported.line, start, imported.line, start + imported.path.length), module.uri));
      }
      for (let lineNumber = 0; lineNumber < document.lineCount; lineNumber += 1) {
        const line = document.lineAt(lineNumber).text;
        const native = line.match(/^\s*native\s+(?:source|link\s+(?:windows|linux|macos))\s+"([^"]+)"/);
        if (!native) continue;
        const target = vscode.Uri.file(path.resolve(path.dirname(document.uri.fsPath), native[1]));
        try {
          await vscode.workspace.fs.stat(target);
          const start = line.indexOf(native[1]);
          links.push(new vscode.DocumentLink(new vscode.Range(lineNumber, start, lineNumber, start + native[1].length), target));
        } catch (_) {
          // Missing native files remain compiler diagnostics rather than broken editor links.
        }
      }
      return links;
    }
  }));

  context.subscriptions.push(vscode.languages.registerFoldingRangeProvider(selector, {
    provideFoldingRanges(document) {
      const result = [];
      const stack = [];
      let commentStart = -1;
      for (let line = 0; line < document.lineCount; line += 1) {
        const text = document.lineAt(line).text;
        if (/^\s*\/\//.test(text)) {
          if (commentStart < 0) commentStart = line;
        } else if (commentStart >= 0) {
          if (line - commentStart > 1) result.push(new vscode.FoldingRange(commentStart, line - 1, vscode.FoldingRangeKind.Comment));
          commentStart = -1;
        }
        const masked = language.maskLine(text);
        for (const char of masked) {
          if (char === '{') stack.push(line);
          else if (char === '}' && stack.length) {
            const start = stack.pop();
            if (line > start) result.push(new vscode.FoldingRange(start, line));
          }
        }
      }
      if (commentStart >= 0 && document.lineCount - commentStart > 1) result.push(new vscode.FoldingRange(commentStart, document.lineCount - 1, vscode.FoldingRangeKind.Comment));
      return result;
    }
  }));

  context.subscriptions.push(vscode.languages.registerCodeActionsProvider(selector, {
    provideCodeActions(document, range, contextInfo) {
      const actions = [];
      const line = document.lineAt(range.start.line).text;
      const oldImport = line.match(/^(\s*)import\s+<std\/([A-Za-z_]\w*)>\s*(?:as\s+([A-Za-z_]\w*))?/);
      if (oldImport) {
        const alias = oldImport[3] || oldImport[2];
        const action = new vscode.CodeAction('Convert to ZyenLang 0.3 import', vscode.CodeActionKind.QuickFix);
        action.edit = new vscode.WorkspaceEdit();
        action.edit.replace(document.uri, document.lineAt(range.start.line).range, `${oldImport[1]}import std::${oldImport[2]} as ${alias}`);
        actions.push(action);
      }
      if (line.includes('FREE__(')) {
        const action = new vscode.CodeAction('Replace FREE__ with DROP__', vscode.CodeActionKind.QuickFix);
        action.edit = new vscode.WorkspaceEdit();
        action.edit.replace(document.uri, document.lineAt(range.start.line).range, line.replace(/\bFREE__\s*\(/g, 'DROP__('));
        actions.push(action);
      }
      const parsed = index.parse(document);
      for (const diagnostic of contextInfo.diagnostics) {
        if (!/use `::`|module/i.test(diagnostic.message)) continue;
        const dot = line.match(/\b([A-Za-z_]\w*)\.([A-Za-z_]\w*)/);
        if (!dot || !parsed.imports.some((item) => item.name === dot[1])) continue;
        const action = new vscode.CodeAction(`Use ${dot[1]}::${dot[2]}`, vscode.CodeActionKind.QuickFix);
        action.diagnostics = [diagnostic];
        action.edit = new vscode.WorkspaceEdit();
        const start = line.indexOf(dot[0]);
        action.edit.replace(document.uri, new vscode.Range(range.start.line, start, range.start.line, start + dot[0].length), `${dot[1]}::${dot[2]}`);
        actions.push(action);
      }
      return actions;
    }
  }, { providedCodeActionKinds: [vscode.CodeActionKind.QuickFix] }));
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
      warning.source = 'zy';
      this.collection.set(document.uri, [warning]);
      this.status.text = '$(warning) ZyenLang';
      return;
    }
    const executable = config.get('compilerPath', 'zy');
    const cwd = projectTools.findProjectRoot(document.uri.fsPath) || vscode.workspace.getWorkspaceFolder(document.uri)?.uri.fsPath || path.dirname(document.uri.fsPath);
    const child = spawn(executable, ['check', '--file', document.uri.fsPath, '--library', '--stdin'], {
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
      this.status.tooltip = `Set zyenlang.compilerPath to the zy executable.\n${error.message}`;
    });
    child.on('close', async (code) => {
      clearTimeout(timer);
      if (this.generations.get(key) !== generation) return;
      this.running.delete(key);
      if (spawnFailed) return;
      if (failureReason) {
        const diagnostic = new vscode.Diagnostic(new vscode.Range(0, 0, 0, 1), failureReason, vscode.DiagnosticSeverity.Error);
        diagnostic.source = 'zy';
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
      diagnostic.source = 'zy';
      const uriString = uri.toString();
      if (!groups.has(uriString)) groups.set(uriString, []);
      groups.get(uriString).push(diagnostic);
    }
    if (!groups.size && output.trim() && !/^OK:/m.test(output)) {
      const diagnostic = new vscode.Diagnostic(new vscode.Range(0, 0, 0, Math.max(1, document.lineAt(0).text.length)), output.trim(), vscode.DiagnosticSeverity.Error);
      diagnostic.source = 'zy';
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

async function processTask(document, args, label, cwdOverride) {
  const config = vscode.workspace.getConfiguration('zyenlang', document.uri);
  const executable = config.get('compilerPath', 'zy');
  const cwd = cwdOverride || projectTools.findProjectRoot(document.uri.fsPath) || vscode.workspace.getWorkspaceFolder(document.uri)?.uri.fsPath || path.dirname(document.uri.fsPath);
  const task = new vscode.Task(
    { type: 'zyenlang', command: args[0] },
    vscode.TaskScope.Workspace,
    label,
    'ZyenLang',
    new vscode.ProcessExecution(executable, args, { cwd }),
    ['$zyenlang']
  );
  task.presentationOptions = { reveal: vscode.TaskRevealKind.Always, panel: vscode.TaskPanelKind.Shared, clear: false };
  await vscode.tasks.executeTask(task);
}

function currentProject(document) {
  const project = projectTools.loadProject(document.uri.fsPath);
  if (!project) vscode.window.showErrorMessage('No zyproject.toml was found above the current file.');
  return project;
}

async function selectTarget(document, project, runnableOnly = false) {
  let targets = project.targets;
  if (runnableOnly) targets = targets.filter((item) => item.kind === 'bin');
  if (!targets.length) {
    vscode.window.showErrorMessage(runnableOnly ? 'This project has no runnable bin target.' : 'This project has no targets.');
    return undefined;
  }
  const configured = vscode.workspace.getConfiguration('zyenlang', document.uri).get('project.target', '');
  const configuredTarget = targets.find((item) => item.name === configured);
  if (configuredTarget) return configuredTarget.name;
  const preferred = targets.find((item) => item.name === project.defaultTarget);
  if (targets.length === 1) return targets[0].name;
  const ordered = preferred ? [preferred, ...targets.filter((item) => item !== preferred)] : targets;
  const picked = await vscode.window.showQuickPick(ordered.map((item) => ({
    label: item.name,
    description: item.kind,
    detail: item.entry,
    picked: item === preferred
  })), { placeHolder: `Select a ZyenLang ${runnableOnly ? 'bin ' : ''}target` });
  return picked?.label;
}

function projectArguments(command, project, target, release) {
  const args = [command, '--project', project.root];
  if (target) args.push(target);
  if (release && ['run', 'build', 'test'].includes(command)) args.push('--release');
  return args;
}

function registerCommands(context, diagnostics, index) {
  context.subscriptions.push(vscode.commands.registerCommand('zyenlang.check', () => {
    const document = vscode.window.activeTextEditor?.document;
    if (document?.languageId === 'zyen') diagnostics.schedule(document, true);
  }));

  context.subscriptions.push(vscode.commands.registerCommand('zyenlang.run', async () => {
    if (!(await requireTrustedWorkspace())) return;
    const document = await saveCurrentDocument();
    if (!document) return;
    const project = currentProject(document);
    if (!project) return;
    const target = await selectTarget(document, project, true);
    if (!target) return;
    const config = vscode.workspace.getConfiguration('zyenlang', document.uri);
    const args = projectArguments('run', project, target, config.get('build.release', true));
    const programArgs = config.get('run.arguments', []);
    if (programArgs.length) args.push('--', ...programArgs);
    await processTask(document, args, `Run ${target}`, project.root);
  }));

  context.subscriptions.push(vscode.commands.registerCommand('zyenlang.build', async () => {
    if (!(await requireTrustedWorkspace())) return;
    const document = await saveCurrentDocument();
    if (!document) return;
    const project = currentProject(document);
    if (!project) return;
    const target = await selectTarget(document, project);
    if (!target) return;
    const release = vscode.workspace.getConfiguration('zyenlang', document.uri).get('build.release', true);
    await processTask(document, projectArguments('build', project, target, release), `Build ${target}`, project.root);
  }));

  context.subscriptions.push(vscode.commands.registerCommand('zyenlang.test', async () => {
    if (!(await requireTrustedWorkspace())) return;
    const document = await saveCurrentDocument();
    if (!document) return;
    const project = currentProject(document);
    if (!project) return;
    const release = vscode.workspace.getConfiguration('zyenlang', document.uri).get('build.release', true);
    await processTask(document, projectArguments('test', project, undefined, release), 'Test project', project.root);
  }));

  context.subscriptions.push(vscode.commands.registerCommand('zyenlang.clean', async () => {
    if (!(await requireTrustedWorkspace())) return;
    const document = await saveCurrentDocument();
    if (!document) return;
    const project = currentProject(document);
    if (!project) return;
    const target = await selectTarget(document, project);
    if (!target) return;
    await processTask(document, projectArguments('clean', project, target, false), `Clean ${target}`, project.root);
  }));

  context.subscriptions.push(vscode.commands.registerCommand('zyenlang.refreshIndex', async () => {
    index.invalidateWorkspace();
    await index.ensureWorkspace();
    vscode.window.showInformationMessage(`ZyenLang index refreshed: ${index.all().length} module(s).`);
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
    await processTask(document, [
      'emit', '--file', document.uri.fsPath, '--kind', 'c', '--out-dir', path.dirname(target.fsPath),
      '--output-name', path.basename(target.fsPath, '.c')
    ], 'Emit C source');
  }));
}

function activate(context) {
  const index = new WorkspaceIndex();
  const diagnostics = new DiagnosticsController(context, index);
  registerLanguageFeatures(context, index);
  registerCommands(context, diagnostics, index);

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
  const manifestWatcher = vscode.workspace.createFileSystemWatcher('**/zyproject.toml');
  manifestWatcher.onDidCreate(() => index.invalidateWorkspace());
  manifestWatcher.onDidChange(() => index.invalidateWorkspace());
  manifestWatcher.onDidDelete(() => index.invalidateWorkspace());
  context.subscriptions.push(manifestWatcher);

  for (const document of vscode.workspace.textDocuments) {
    if (document.languageId === 'zyen') {
      index.parse(document);
      diagnostics.schedule(document, true);
    }
  }
}

function deactivate() {}

module.exports = { activate, deactivate };
