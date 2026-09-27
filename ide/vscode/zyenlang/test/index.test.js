'use strict';

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const Module = require('module');

function uriFor(filePath) {
  const fsPath = path.resolve(filePath);
  return {
    fsPath,
    toString() { return `file:///${fsPath.replace(/\\/g, '/')}`; }
  };
}

class Position {
  constructor(line, character) {
    this.line = line;
    this.character = character;
  }
}

class Range {
  constructor(startLine, startCharacter, endLine, endCharacter) {
    if (startLine instanceof Position) {
      this.start = startLine;
      this.end = startCharacter;
    } else {
      this.start = new Position(startLine, startCharacter);
      this.end = new Position(endLine, endCharacter);
    }
  }

  isEqual(other) {
    return this.start.line === other.start.line && this.start.character === other.start.character &&
      this.end.line === other.end.line && this.end.character === other.end.character;
  }
}

class Location {
  constructor(uri, range) {
    this.uri = uri;
    this.range = range;
  }
}

const vscode = {
  Position,
  Range,
  Location,
  Uri: { file: uriFor },
  workspace: {
    isTrusted: true,
    workspaceFolders: [],
    fs: {
      async stat(uri) {
        const value = await fs.promises.stat(uri.fsPath);
        return { size: value.size, mtime: value.mtimeMs };
      },
      readFile(uri) { return fs.promises.readFile(uri.fsPath); }
    },
    getConfiguration() {
      return {
        get(name, fallback) {
          if (name === 'compilerPath') return process.env.ZYENLANG_TEST_COMPILER || 'zy';
          if (name === 'stdlibPath') return '';
          return fallback;
        }
      };
    },
    findFiles: async () => [],
    asRelativePath(uri) { return uri.fsPath; }
  }
};

const originalLoad = Module._load;
Module._load = function load(request, parent, isMain) {
  if (request === 'vscode') return vscode;
  return originalLoad.call(this, request, parent, isMain);
};
const { _test } = require('../extension');
Module._load = originalLoad;

function documentFor(filePath, text) {
  const lines = text.split(/\r?\n/);
  return {
    uri: uriFor(filePath),
    version: 1,
    getText() { return text; },
    lineAt(line) { return { text: lines[line] }; }
  };
}

(async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'zyenlang-index-'));
  try {
    const sourcePath = path.join(root, 'main.zy');
    const source = `import std::io as io
import std::gui as gui

fn main() i32 {
    io::print("hello")
    let app = gui::application("Demo", 800, 480)
    app.open()
    return helper()
}

fn helper() i32 {
    return 0
}
`;
    fs.writeFileSync(sourcePath, source);
    const document = documentFor(sourcePath, source);
    const index = new _test.WorkspaceIndex();
    const parsed = index.parse(document);

    const ioImport = parsed.imports.find((item) => item.name === 'io');
    const ioModule = await index.moduleFor(document, ioImport.path);
    assert(ioModule, 'std::io should resolve through the compiler installation');
    assert(ioModule.uri.fsPath.replace(/\\/g, '/').endsWith('/compiler/std/io.zy'));

    const ioExports = await index.moduleExports(document, ioImport);
    const print = ioExports.find((item) => item.symbol.name === 'print');
    assert(print && !print.symbol.builtin, 'real std source should win over builtin fallback symbols');
    assert.strictEqual(print.symbol.parameters, 'text: str');

    const printLine = source.split(/\r?\n/).findIndex((line) => line.includes('io::print'));
    const printResolved = await index.resolve(document, new Position(printLine, source.split(/\r?\n/)[printLine].indexOf('print') + 1));
    assert(printResolved && !printResolved.symbol.builtin);
    assert(printResolved.uri.fsPath.replace(/\\/g, '/').endsWith('/compiler/std/io.zy'));
    const printReferences = await index.referencesFor(document, printResolved, true);
    assert(printReferences.some((item) => item.uri.toString() === document.uri.toString() && item.range.start.line === printLine));

    const applicationMembers = await index.membersOf('Application', document);
    const open = applicationMembers.find((item) => item.symbol.name === 'open');
    assert(open && !open.symbol.builtin, 'inferred std class members should retain source locations');
    assert(open.uri.fsPath.replace(/\\/g, '/').endsWith('/compiler/std/gui.zy'));
    assert(!applicationMembers.some((item) => item.symbol.name === 'title'), 'private imported fields stay hidden');
    assert(applicationMembers.some((item) => item.symbol.name === 'fps'), 'public imported fields are indexed');

    const openLine = source.split(/\r?\n/).findIndex((line) => line.includes('app.open'));
    const openResolved = await index.resolve(document, new Position(openLine, source.split(/\r?\n/)[openLine].indexOf('open') + 1));
    assert(openResolved && !openResolved.symbol.builtin);
    assert(openResolved.uri.fsPath.replace(/\\/g, '/').endsWith('/compiler/std/gui.zy'));

    const helper = index.visibleLocal(parsed, 'helper', printLine);
    assert(helper && helper.kind === 'function', 'top-level declarations should resolve before their source line');
    assert.deepStrictEqual(_test.signatureParameters({ detail: 'fn map(callback: fn(i32) str, value: i32) str' }), [
      'callback: fn(i32) str',
      'value: i32'
    ]);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
  console.log('index tests passed');
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
