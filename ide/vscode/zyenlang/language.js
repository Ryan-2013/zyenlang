'use strict';

const KEYWORDS = [
  'as', 'await', 'break', 'catch', 'continue', 'else', 'false', 'fn', 'if',
  'import', 'let', 'mut', 'native', 'null', 'private', 'public', 'recover', 'return',
  'source', 'spawn', 'stop', 'struct', 'throws', 'true', 'TYPEOF__',
  'LIST_LEN__', 'LIST_PUSH__', 'LIST_SET__', 'while'
];

const TYPES = [
  'bool', 'Error', 'f32', 'f64', 'i8', 'i16', 'i32', 'i64', 'isize',
  'Box', 'Channel', 'List', 'Raw', 'Ref', 'str', 'Task', 'u8', 'u16', 'u32',
  'u64', 'usize', 'void'
];

const SPECIAL_VALUES = ['GET_ARGS__', 'GET_EXE__'];

const SPECIAL_FORMS = [
  {
    name: 'LIST_LEN__',
    detail: 'LIST_LEN__(list: List<T>) usize',
    snippet: 'LIST_LEN__(${1:list})',
    documentation: 'Return the number of elements in a built-in List<T>.'
  },
  {
    name: 'LIST_PUSH__',
    detail: 'LIST_PUSH__(list: List<T>, value: T) void',
    snippet: 'LIST_PUSH__(${1:list}, ${2:value})',
    documentation: 'Append a strongly typed value to a local List<T> variable.'
  },
  {
    name: 'LIST_SET__',
    detail: 'LIST_SET__(list: List<T>, index: i32, value: T) void throws Error',
    snippet: 'LIST_SET__(${1:list}, ${2:index}, ${3:value})',
    documentation: 'Replace a List<T> element with checked bounds.'
  },
  {
    name: 'TYPEOF__',
    detail: 'TYPEOF__(value, Type) bool',
    snippet: 'TYPEOF__(${1:value}, ${2:Type})',
    documentation: 'Compare static types at compile time without evaluating the value.'
  }
];

const BUILTINS = {
  io: [
    ['print', 'fn print(value: str) void'],
    ['eprint', 'fn eprint(value: str) void']
  ],
  list: [
    ['length', 'fn length<T>(values: List<T>) usize'],
    ['is_empty', 'fn is_empty<T>(values: List<T>) bool']
  ],
  process: [
    ['args', 'fn args(value: List<str>) List<str>'],
    ['executable', 'fn executable(value: str) str']
  ],
  path: [
    ['separator', 'fn separator() str'],
    ['normalize', 'fn normalize(value: str) str'],
    ['join', 'fn join(left: str, right: str) str'],
    ['basename', 'fn basename(value: str) str'],
    ['dirname', 'fn dirname(value: str) str'],
    ['parent', 'fn parent(value: str) str'],
    ['extension', 'fn extension(value: str) str'],
    ['stem', 'fn stem(value: str) str'],
    ['with_extension', 'fn with_extension(value: str, next_extension: str) str'],
    ['is_absolute', 'fn is_absolute(value: str) bool'],
    ['exists', 'fn exists(value: str) bool'],
    ['is_file', 'fn is_file(value: str) bool'],
    ['is_dir', 'fn is_dir(value: str) bool']
  ],
  thread: [
    ['sleep_ms', 'fn sleep_ms(milliseconds: i32) i32'],
    ['yield_now', 'fn yield_now() i32'],
    ['cpu_count', 'fn cpu_count() i32']
  ],
  request: [
    ['get', 'fn get(url: str) Response'],
    ['get_with_timeout', 'fn get_with_timeout(url: str, timeout_ms: i32) Response'],
    ['post', 'fn post(url: str, body: str) Response'],
    ['post_with_timeout', 'fn post_with_timeout(url: str, body: str, content_type: str, timeout_ms: i32) Response'],
    ['post_json', 'fn post_json(url: str, json: str) Response'],
    ['put', 'fn put(url: str, body: str) Response'],
    ['put_with_timeout', 'fn put_with_timeout(url: str, body: str, content_type: str, timeout_ms: i32) Response'],
    ['delete', 'fn delete(url: str) Response'],
    ['download', 'fn download(url: str, path: str) Response'],
    ['download_with_timeout', 'fn download_with_timeout(url: str, path: str, timeout_ms: i32) Response'],
    ['send', 'fn send(method: str, url: str, body: str, content_type: str, timeout_ms: i32) Response']
  ],
  server: [
    ['serve_once', 'fn serve_once(host: str, port: i32, body: str) i32 throws Error'],
    ['serve', 'fn serve(host: str, port: i32, body: str, max_requests: i32) i32 throws Error']
  ],
  gui: [
    ['window', 'fn window(title: str, width: i32, height: i32) Window'],
    ['window_colored', 'fn window_colored(title: str, width: i32, height: i32, background: str) Window'],
    ['pick_file', 'fn pick_file() i32'],
    ['line', 'fn line(x1: i32, y1: i32, x2: i32, y2: i32, color: str, width: i32) i32'],
    ['rect', 'fn rect(x: i32, y: i32, width: i32, height: i32, color: str) i32'],
    ['circle', 'fn circle(x: i32, y: i32, radius: i32, color: str) i32'],
    ['text', 'fn text(x: i32, y: i32, value: str, color: str, size: i32) i32'],
    ['code_view', 'fn code_view(x: i32, y: i32, width: i32, height: i32, first_line: i32, line_height: i32, char_width: i32, size: i32, stamp: i32, lines: str) i32']
  ],
  error: [
    ['require', 'fn require(condition: bool, message: str) void throws Error']
  ],
  option: [
    ['is_null', 'fn is_null<T>(value: T | null) bool'],
    ['is_some', 'fn is_some<T>(value: T | null) bool']
  ]
};

function maskLine(line) {
  let result = '';
  let inString = false;
  let escaped = false;
  for (let i = 0; i < line.length; i += 1) {
    const char = line[i];
    if (!inString && char === '/' && line[i + 1] === '/') {
      result += ' '.repeat(line.length - i);
      break;
    }
    if (char === '"' && !escaped) inString = !inString;
    result += inString ? ' ' : char;
    escaped = char === '\\' && !escaped;
    if (char !== '\\') escaped = false;
  }
  return result.padEnd(line.length, ' ');
}

function splitTopLevel(value) {
  const parts = [];
  let start = 0;
  let depth = 0;
  for (let i = 0; i < value.length; i += 1) {
    const char = value[i];
    if ('(<[{'.includes(char)) depth += 1;
    else if (')>]}'.includes(char)) depth -= 1;
    else if (char === ',' && depth === 0) {
      parts.push(value.slice(start, i).trim());
      start = i + 1;
    }
  }
  const tail = value.slice(start).trim();
  if (tail) parts.push(tail);
  return parts;
}

function parameterType(value) {
  let depth = 0;
  for (let index = 0; index < value.length; index += 1) {
    const char = value[index];
    if ('(<[{'.includes(char)) depth += 1;
    else if (')>]}'.includes(char)) depth -= 1;
    else if (char === '=' && depth === 0) return value.slice(0, index).trim();
  }
  return value.trim();
}

function parameterSymbols(parameters, line, baseColumn, container) {
  const result = [];
  let searchFrom = 0;
  for (const parameter of splitTopLevel(parameters)) {
    const match = parameter.match(/^(?:(mut)\s+)?([A-Za-z_]\w*)\s*:\s*(.+)$/);
    if (!match) continue;
    const name = match[2];
    const type = parameterType(match[3]);
    const relative = parameters.indexOf(name, searchFrom);
    searchFrom = relative + name.length;
    result.push({
      name,
      kind: 'parameter',
      type,
      detail: `${match[1] ? 'mut ' : ''}${name}: ${type}`,
      line,
      column: baseColumn + relative,
      endColumn: baseColumn + relative + name.length,
      container
    });
  }
  return result;
}

function precedingDocs(lines, lineNumber) {
  const docs = [];
  for (let index = lineNumber - 1; index >= 0; index -= 1) {
    const line = lines[index].trim();
    if (!line.startsWith('//')) break;
    docs.unshift(line.replace(/^\/\/\/?\s?/, ''));
  }
  return docs.join('\n');
}

function addSymbol(result, symbol) {
  result.symbols.push(symbol);
  if (['struct', 'function', 'method', 'field', 'native'].includes(symbol.kind)) {
    result.exports.push(symbol);
  }
}

function parseDocument(text, uri = '') {
  const lines = text.split(/\r?\n/);
  const maskedLines = lines.map(maskLine);
  const result = { uri, imports: [], symbols: [], exports: [], lines, maskedLines };
  let depth = 0;
  let activeStruct = null;
  let activeFunction = null;

  for (let lineNumber = 0; lineNumber < lines.length; lineNumber += 1) {
    const original = lines[lineNumber];
    const line = maskedLines[lineNumber];
    const trimmed = line.trim();

    const importMatch = original.match(/^\s*import\s+(<([^>]+)>|"([^"]+)")\s*(?:as\s+([A-Za-z_]\w*))?/);
    if (importMatch) {
      const path = importMatch[2] || importMatch[3];
      const alias = importMatch[4] || path.split('/').pop().replace(/\.zy$/, '');
      const column = original.indexOf(alias);
      const symbol = {
        name: alias,
        kind: 'import',
        path,
        detail: `import ${importMatch[1]} as ${alias}`,
        line: lineNumber,
        column: Math.max(0, column),
        endColumn: Math.max(0, column) + alias.length
      };
      result.imports.push(symbol);
      result.symbols.push(symbol);
    }

    const structMatch = line.match(/^\s*(?:(public|private)\s+)?struct\s+([A-Za-z_]\w*)/);
    if (structMatch) {
      const name = structMatch[2];
      const column = original.indexOf(name);
      activeStruct = { name, depth: depth + 1 };
      addSymbol(result, {
        name,
        kind: 'struct',
        visibility: structMatch[1] || 'public',
        detail: `${structMatch[1] ? `${structMatch[1]} ` : ''}struct ${name}`,
        documentation: precedingDocs(lines, lineNumber),
        line: lineNumber,
        column,
        endColumn: column + name.length
      });
    }

    const functionMatch = line.match(/^\s*(?:(public|private)\s+)?(?:(native)\s+)?fn\s+(?:\(\s*([A-Za-z_]\w*)\s*:\s*([^\)]+)\s*\)\s*)?([A-Za-z_]\w*)(?:\s*<[^>]+>)?\s*\(([^)]*)\)\s*([^\{=]*?)(?:\s*\{|\s*=|\s*$)/);
    if (functionMatch) {
      const receiverName = functionMatch[3];
      const receiverType = functionMatch[4] && functionMatch[4].trim();
      const name = functionMatch[5];
      const parameters = functionMatch[6];
      const returnAndThrows = functionMatch[7].trim();
      const column = original.indexOf(name, original.indexOf('fn') + 2);
      const kind = functionMatch[2] ? 'native' : receiverType ? 'method' : 'function';
      const signature = original.trim().replace(/\s*\{\s*$/, '').replace(/\s*=\s*"[^"]*"\s*$/, '');
      const symbol = {
        name,
        kind,
        visibility: functionMatch[1] || 'public',
        receiverName,
        receiverType,
        parameters,
        returnType: returnAndThrows.replace(/\s+throws\s+Error\s*$/, '').trim() || 'void',
        throws: /\bthrows\s+Error\b/.test(returnAndThrows),
        detail: signature,
        documentation: precedingDocs(lines, lineNumber),
        line: lineNumber,
        column,
        endColumn: column + name.length,
        container: receiverType || undefined
      };
      addSymbol(result, symbol);
      activeFunction = trimmed.endsWith('{') ? { name, depth: depth + 1 } : null;
      if (receiverName) {
        result.symbols.push({
          name: receiverName,
          kind: 'parameter',
          type: receiverType,
          detail: `${receiverName}: ${receiverType}`,
          line: lineNumber,
          column: original.indexOf(receiverName),
          endColumn: original.indexOf(receiverName) + receiverName.length,
          container: name
        });
      }
      const parameterStart = original.indexOf('(', column + name.length) + 1;
      result.symbols.push(...parameterSymbols(parameters, lineNumber, parameterStart, name));
    }

    if (activeStruct && depth === activeStruct.depth && !functionMatch) {
      const fieldMatch = line.match(/^\s*(?:(public|private)\s+)?(?:let\s+(?:this\.)?)?([A-Za-z_]\w*)\s*:\s*([^=\n]+?)(?:\s*=.*)?$/);
      if (fieldMatch && !/^(let|fn|struct|import)\b/.test(trimmed)) {
        const name = fieldMatch[2];
        const column = original.indexOf(name);
        addSymbol(result, {
          name,
          kind: 'field',
          type: fieldMatch[3].trim(),
          visibility: fieldMatch[1] || 'public',
          detail: `${fieldMatch[1] ? `${fieldMatch[1]} ` : ''}${name}: ${fieldMatch[3].trim()}`,
          documentation: precedingDocs(lines, lineNumber),
          line: lineNumber,
          column,
          endColumn: column + name.length,
          container: activeStruct.name
        });
      }
    }

    const letTuple = line.match(/\blet\s*\(([^)]+)\)\s*=/);
    if (letTuple) {
      const tupleStart = original.indexOf(letTuple[1]);
      for (const part of splitTopLevel(letTuple[1])) {
        const match = part.match(/^([A-Za-z_]\w*)\s*(?::\s*(.+))?$/);
        if (!match) continue;
        const column = original.indexOf(match[1], tupleStart);
        result.symbols.push({
          name: match[1], kind: 'variable', type: match[2] || '',
          detail: match[2] ? `${match[1]}: ${match[2]}` : match[1],
          line: lineNumber, column, endColumn: column + match[1].length,
          container: activeFunction && activeFunction.name
        });
      }
    } else {
      const letMatch = line.match(/\blet\s+([A-Za-z_]\w*)\s*(?::\s*([^=]+?))?\s*=/);
      if (letMatch) {
        const name = letMatch[1];
        const column = original.indexOf(name, original.indexOf('let') + 3);
        result.symbols.push({
          name, kind: 'variable', type: (letMatch[2] || '').trim(),
          detail: letMatch[2] ? `${name}: ${letMatch[2].trim()}` : name,
          line: lineNumber, column, endColumn: column + name.length,
          container: activeFunction && activeFunction.name
        });
      }
    }

    let opens = 0;
    let closes = 0;
    for (const char of line) {
      if (char === '{') opens += 1;
      else if (char === '}') closes += 1;
    }
    depth += opens - closes;
    if (activeFunction && depth < activeFunction.depth) activeFunction = null;
    if (activeStruct && depth < activeStruct.depth) activeStruct = null;
  }
  return result;
}

function wordAt(line, column) {
  const left = line.slice(0, column).match(/[A-Za-z_]\w*$/);
  const right = line.slice(column).match(/^\w*/);
  if (!left && (!right || !right[0])) return null;
  const start = left ? column - left[0].length : column;
  const value = (left ? left[0] : '') + (right ? right[0] : '');
  return { value, start, end: start + value.length };
}

function qualifierAt(line, column) {
  const before = line.slice(0, column);
  const match = before.match(/([A-Za-z_]\w*)\.([A-Za-z_]\w*)?$/);
  return match ? { qualifier: match[1], prefix: match[2] || '' } : null;
}

function callAt(line, column) {
  const before = line.slice(0, column);
  let depth = 0;
  let comma = 0;
  for (let i = before.length - 1; i >= 0; i -= 1) {
    const char = before[i];
    if (char === ')') depth += 1;
    else if (char === '(') {
      if (depth > 0) depth -= 1;
      else {
        const nameMatch = before.slice(0, i).match(/([A-Za-z_]\w*)\s*$/);
        return nameMatch ? { name: nameMatch[1], activeParameter: comma } : null;
      }
    } else if (char === ',' && depth === 0) comma += 1;
  }
  return null;
}

function importPathAt(line, column) {
  const before = line.slice(0, column);
  const standard = before.match(/^\s*import\s+<std\/([A-Za-z_]\w*)?$/);
  if (standard) return { kind: 'std', prefix: standard[1] || '' };
  return null;
}

module.exports = {
  BUILTINS,
  KEYWORDS,
  SPECIAL_FORMS,
  SPECIAL_VALUES,
  TYPES,
  callAt,
  importPathAt,
  parseDocument,
  qualifierAt,
  splitTopLevel,
  wordAt
};
