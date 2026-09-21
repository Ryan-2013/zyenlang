'use strict';

const KEYWORDS = [
  'as', 'await', 'break', 'catch', 'class', 'continue', 'defer', 'deinit', 'else',
  'export', 'false', 'fn', 'if', 'import', 'init', 'let', 'mut', 'native', 'null',
  'private', 'public', 'recover', 'return', 'source', 'spawn', 'static', 'stop',
  'struct', 'throws', 'true', 'while', 'TYPEOF__', 'CLONE__', 'CLONE_REF__',
  'DROP__', 'REF_SET__', 'LIST_LEN__', 'LIST_GET__', 'LIST_PUSH__', 'LIST_SET__',
  'LIST_POP__', 'LIST_CLEAR__', 'PRINT_CMD__', 'STR_TO_LIST__', 'STR_LEN__',
  'STR_BYTE_LEN__', 'STR_GET__', 'STR_SLICE__', 'FILE__', 'GET_ARGS__', 'GET_EXE__'
];

const TYPES = [
  'bool', 'Error', 'f32', 'f64', 'i8', 'i16', 'i32', 'i64', 'isize',
  'Box', 'Channel', 'List', 'Raw', 'Ref', 'str', 'Task', 'u8', 'u16', 'u32',
  'u64', 'usize', 'void'
];

const SPECIAL_VALUES = [];

const SPECIAL_FORMS = [
  {
    name: 'DROP__',
    detail: 'DROP__(local) void',
    snippet: 'DROP__(${1:local})',
    documentation: 'Run normal cleanup early and leave the binding uninitialized until assignment.'
  },
  {
    name: 'CLONE__',
    detail: 'CLONE__(value) T',
    snippet: 'CLONE__(${1:value})',
    documentation: 'Explicitly clone a value. ARC classes retain identity; structs preserve value semantics.'
  },
  {
    name: 'CLONE_REF__',
    detail: 'CLONE_REF__(reference: &T) T',
    snippet: 'CLONE_REF__(${1:reference})',
    documentation: 'Copy the value addressed by a safe readonly or mutable reference.'
  },
  {
    name: 'REF_SET__',
    detail: 'REF_SET__(reference: &mut T, value: T) void',
    snippet: 'REF_SET__(${1:reference}, ${2:value})',
    documentation: 'Assign through a unique mutable reference.'
  },
  {
    name: 'LIST_LEN__',
    detail: 'LIST_LEN__(list: List<T> | &List<T> | &mut List<T>) usize',
    snippet: 'LIST_LEN__(${1:list})',
    documentation: 'Return the number of elements without cloning List storage.'
  },
  {
    name: 'LIST_PUSH__',
    detail: 'LIST_PUSH__(list: List<T> | &mut List<T>, value: T) void',
    snippet: 'LIST_PUSH__(${1:list}, ${2:value})',
    documentation: 'Append to an owned List place or mutate the caller through &mut List<T>.'
  },
  {
    name: 'LIST_SET__',
    detail: 'LIST_SET__(list: List<T> | &mut List<T>, index: i32, value: T) void throws Error',
    snippet: 'LIST_SET__(${1:list}, ${2:index}, ${3:value})',
    documentation: 'Replace a List<T> element with checked bounds.'
  },
  {
    name: 'LIST_GET__',
    detail: 'LIST_GET__(list: List<T> | &List<T> | &mut List<T>, index: i32) T throws Error',
    snippet: 'LIST_GET__(${1:list}, ${2:index})',
    documentation: 'Read a checked List<T> element.'
  },
  {
    name: 'PRINT_CMD__',
    detail: 'PRINT_CMD__(text: str, color: str) void',
    snippet: 'PRINT_CMD__(${1:text}, "${2:#FFFFFF}")',
    documentation: 'Write one line using a #RRGGBB terminal color when color output is enabled.'
  },
  {
    name: 'LIST_POP__',
    detail: 'LIST_POP__(list: List<T> | &mut List<T>) T throws Error',
    snippet: 'LIST_POP__(${1:list})',
    documentation: 'Remove and return the last List<T> element.'
  },
  {
    name: 'LIST_CLEAR__',
    detail: 'LIST_CLEAR__(list: List<T> | &mut List<T>) void',
    snippet: 'LIST_CLEAR__(${1:list})',
    documentation: 'Clear a List<T> after copy-on-write detachment.'
  },
  {
    name: 'STR_TO_LIST__',
    detail: 'STR_TO_LIST__(text: str) List<str>',
    snippet: 'STR_TO_LIST__(${1:text})',
    documentation: 'Split UTF-8 text into an ARC-managed List with one Unicode character per element.'
  },
  {
    name: 'TYPEOF__',
    detail: 'TYPEOF__(value, Type) bool',
    snippet: 'TYPEOF__(${1:value}, ${2:Type})',
    documentation: 'Compare static types at compile time without evaluating the value.'
  },
  {
    name: 'FILE__', detail: 'FILE__() str', snippet: 'FILE__()',
    documentation: 'Return the absolute source path of the module containing the call.'
  },
  {
    name: 'GET_ARGS__', detail: 'GET_ARGS__() List<str>', snippet: 'GET_ARGS__()',
    documentation: 'Return process arguments. This intrinsic is valid from main.'
  },
  {
    name: 'GET_EXE__', detail: 'GET_EXE__() str', snippet: 'GET_EXE__()',
    documentation: 'Return the running executable path. This intrinsic is valid from main.'
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
  fs: [
    ['read_text', 'fn read_text(path: str) str throws Error'],
    ['write_text', 'fn write_text(path: str, value: str) i32 throws Error'],
    ['append_text', 'fn append_text(path: str, value: str) i32 throws Error'],
    ['tree', 'fn tree(path: str) str throws Error']
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
    ['application', 'fn application(title: str, width: i32, height: i32) Application'],
    ['application_colored', 'fn application_colored(title: str, width: i32, height: i32, background: str) Application'],
    ['event_kind', 'fn event_kind(event: str) i32'],
    ['event_x', 'fn event_x(event: str) i32'],
    ['event_y', 'fn event_y(event: str) i32']
  ],
  error: [
    ['require', 'fn require(condition: bool, message: str) void throws Error']
  ],
  option: [
    ['is_null', 'fn is_null<T>(value: T | null) bool'],
    ['is_some', 'fn is_some<T>(value: T | null) bool']
  ]
};

const BUILTIN_TYPES = {
  c_module: [
    ['Module', 'c_module::Module (template-dependent native module)']
  ]
};

const BUILTIN_MEMBERS = {
  Application: [
    { name: 'open', kind: 'method', detail: 'mut fn open() i32', returnType: 'i32' },
    { name: 'begin_frame', kind: 'method', detail: 'fn begin_frame() i32', returnType: 'i32' },
    { name: 'present', kind: 'method', detail: 'fn present() i32', returnType: 'i32' },
    { name: 'next_event', kind: 'method', detail: 'fn next_event(timeout_ms: i32) str', returnType: 'str' },
    { name: 'close', kind: 'method', detail: 'mut fn close() i32', returnType: 'i32' },
    { name: 'panel', kind: 'method', detail: 'fn panel(x: i32, y: i32, width: i32, height: i32) Panel', returnType: 'Panel' },
    { name: 'label', kind: 'method', detail: 'fn label(x: i32, y: i32, value: str) Label', returnType: 'Label' },
    { name: 'button', kind: 'method', detail: 'fn button(x: i32, y: i32, width: i32, height: i32, label: str, on_click: fn() void) Button', returnType: 'Button' },
    { name: 'button_group', kind: 'method', detail: 'fn button_group() ButtonGroup', returnType: 'ButtonGroup' },
    { name: 'column', kind: 'method', detail: 'fn column(x: i32, y: i32, width: i32, row_height: i32, gap: i32) Column', returnType: 'Column' },
    { name: 'pick_file', kind: 'method', detail: 'fn pick_file() i32', returnType: 'i32' },
    { name: 'line', kind: 'method', detail: 'fn line(x1: i32, y1: i32, x2: i32, y2: i32, color: str, width: i32) i32', returnType: 'i32' },
    { name: 'rect', kind: 'method', detail: 'fn rect(x: i32, y: i32, width: i32, height: i32, color: str) i32', returnType: 'i32' },
    { name: 'circle', kind: 'method', detail: 'fn circle(x: i32, y: i32, radius: i32, color: str) i32', returnType: 'i32' },
    { name: 'text', kind: 'method', detail: 'fn text(x: i32, y: i32, value: str, color: str, size: i32) i32', returnType: 'i32' },
    { name: 'code_view', kind: 'method', detail: 'fn code_view(x: i32, y: i32, width: i32, height: i32, first_line: i32, line_height: i32, char_width: i32, size: i32, stamp: i32, lines: str) i32', returnType: 'i32' },
    { name: 'code_editor', kind: 'method', detail: 'fn code_editor(x: i32, y: i32, width: i32, height: i32, first_line: i32, line_height: i32, char_width: i32, size: i32, stamp: i32, lines: str, selection_start_line: i32, selection_start_column: i32, selection_end_line: i32, selection_end_column: i32) i32', returnType: 'i32' },
    { name: 'completion', kind: 'method', detail: 'fn completion(x: i32, y: i32, width: i32, row_height: i32, size: i32, selected: i32, items: str) i32', returnType: 'i32' },
    { name: 'event_kind', kind: 'method', detail: 'fn event_kind(event: str) i32', returnType: 'i32' },
    { name: 'event_x', kind: 'method', detail: 'fn event_x(event: str) i32', returnType: 'i32' },
    { name: 'event_y', kind: 'method', detail: 'fn event_y(event: str) i32', returnType: 'i32' }
  ],
  Panel: [
    { name: 'draw', kind: 'method', detail: 'fn draw() i32', returnType: 'i32' }
  ],
  Label: [
    { name: 'draw', kind: 'method', detail: 'fn draw() i32', returnType: 'i32' }
  ],
  Button: [
    { name: 'contains', kind: 'method', detail: 'fn contains(x: i32, y: i32) bool', returnType: 'bool' },
    { name: 'draw', kind: 'method', detail: 'fn draw() i32', returnType: 'i32' },
    { name: 'handle', kind: 'method', detail: 'fn handle(event: str) bool', returnType: 'bool' }
  ],
  ButtonGroup: [
    { name: 'add', kind: 'method', detail: 'mut fn add(button: Button) void', returnType: 'void' },
    { name: 'draw', kind: 'method', detail: 'fn draw() i32 throws Error', returnType: 'i32' },
    { name: 'handle', kind: 'method', detail: 'fn handle(event: str) bool throws Error', returnType: 'bool' }
  ],
  Column: [
    { name: 'item_y', kind: 'method', detail: 'fn item_y(index: i32) i32', returnType: 'i32' },
    { name: 'button_at', kind: 'method', detail: 'fn button_at(index: i32, label: str, on_click: fn() void) Button', returnType: 'Button' },
    { name: 'label_at', kind: 'method', detail: 'fn label_at(index: i32, value: str) Label', returnType: 'Label' }
  ]
};

const STANDARD_MODULES = [...new Set([...Object.keys(BUILTINS), ...Object.keys(BUILTIN_TYPES), 'editor'])].sort();

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

function closingDelimiter(value, start, open, close) {
  let depth = 0;
  for (let index = start; index < value.length; index += 1) {
    if (value[index] === open) depth += 1;
    else if (value[index] === close) {
      depth -= 1;
      if (depth === 0) return index;
    }
  }
  return -1;
}

function parseFunctionHeader(line) {
  const prefix = line.match(/^\s*(?:(public|private)\s+)?(?:(export)\s+)?(?:(native)\s+)?(?:(static|mut)\s+)?fn\s+/);
  if (!prefix) return null;
  let cursor = prefix[0].length;
  let receiverName;
  let receiverType;
  if (line[cursor] === '(') {
    const receiverEnd = closingDelimiter(line, cursor, '(', ')');
    if (receiverEnd < 0) return null;
    const receiver = line.slice(cursor + 1, receiverEnd).match(/^\s*([A-Za-z_]\w*)\s*:\s*(.+?)\s*$/);
    if (!receiver) return null;
    receiverName = receiver[1];
    receiverType = receiver[2];
    cursor = receiverEnd + 1;
    while (/\s/.test(line[cursor] || '')) cursor += 1;
  }
  const nameMatch = line.slice(cursor).match(/^([A-Za-z_]\w*)/);
  if (!nameMatch) return null;
  const name = nameMatch[1];
  const nameStart = cursor;
  cursor += name.length;
  while (/\s/.test(line[cursor] || '')) cursor += 1;
  if (line[cursor] === '<') {
    const genericEnd = closingDelimiter(line, cursor, '<', '>');
    if (genericEnd < 0) return null;
    cursor = genericEnd + 1;
    while (/\s/.test(line[cursor] || '')) cursor += 1;
  }
  if (line[cursor] !== '(') return null;
  const parameterStart = cursor + 1;
  const parameterEnd = closingDelimiter(line, cursor, '(', ')');
  if (parameterEnd < 0) return null;
  const parameters = line.slice(parameterStart, parameterEnd);
  const returnAndThrows = line
    .slice(parameterEnd + 1)
    .replace(/\s*\{.*$/, '')
    .replace(/\s*=.*$/, '')
    .trim();
  return {
    visibility: prefix[1] || (prefix[2] ? 'public' : 'private'),
    exported: Boolean(prefix[2]),
    native: Boolean(prefix[3]),
    modifier: prefix[4] || '',
    receiverName,
    receiverType,
    name,
    nameStart,
    parameterStart,
    parameters,
    returnAndThrows
  };
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
  if (['struct', 'class', 'function', 'method', 'field', 'native'].includes(symbol.kind)) {
    result.exports.push(symbol);
  }
}

function parseDocument(text, uri = '') {
  const lines = text.split(/\r?\n/);
  const maskedLines = lines.map(maskLine);
  const result = { uri, imports: [], symbols: [], exports: [], lines, maskedLines, functions: [] };
  let depth = 0;
  let activeType = null;
  let activeFunction = null;

  for (let lineNumber = 0; lineNumber < lines.length; lineNumber += 1) {
    const original = lines[lineNumber];
    const line = maskedLines[lineNumber];
    const trimmed = line.trim();

    const importMatch = original.match(/^\s*import\s+([A-Za-z_]\w*(?:::[A-Za-z_]\w*)*)(?:\s+as\s+([A-Za-z_]\w*))?/);
    if (importMatch) {
      const path = importMatch[1];
      const alias = importMatch[2] || path.split('::').pop();
      const column = original.indexOf(alias);
      const symbol = {
        name: alias,
        kind: 'import',
        path,
        detail: `import ${path} as ${alias}`,
        line: lineNumber,
        column: Math.max(0, column),
        endColumn: Math.max(0, column) + alias.length
      };
      result.imports.push(symbol);
      result.symbols.push(symbol);
    }

    const typeMatch = line.match(/^\s*(?:(public|private)\s+)?(struct|class)\s+([A-Za-z_]\w*)(?:\s*<[^>]+>)?/);
    if (typeMatch) {
      const name = typeMatch[3];
      const column = original.indexOf(name);
      activeType = { name, kind: typeMatch[2], depth: depth + 1 };
      addSymbol(result, {
        name,
        kind: typeMatch[2],
        visibility: typeMatch[1] || 'private',
        detail: `${typeMatch[1] ? `${typeMatch[1]} ` : ''}${typeMatch[2]} ${name}`,
        documentation: precedingDocs(lines, lineNumber),
        line: lineNumber,
        column,
        endColumn: column + name.length
      });
    }

    const functionMatch = parseFunctionHeader(line);
    if (functionMatch) {
      const { receiverName, receiverType, name, parameters, returnAndThrows } = functionMatch;
      const column = functionMatch.nameStart;
      const classMethod = activeType && activeType.kind === 'class' && depth === activeType.depth;
      const kind = functionMatch.native ? 'native' : (classMethod || receiverType) ? 'method' : 'function';
      const signature = original.trim().replace(/\s*\{\s*$/, '').replace(/\s*=\s*"[^"]*"\s*$/, '');
      const symbol = {
        name,
        kind,
        visibility: functionMatch.visibility,
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
        container: classMethod ? activeType.name : receiverType || undefined,
        static: functionMatch.modifier === 'static',
        mutable: functionMatch.modifier === 'mut',
        exported: functionMatch.exported
      };
      addSymbol(result, symbol);
      activeFunction = line.includes('{') ? { name, depth: depth + 1, startLine: lineNumber } : null;
      if (activeFunction) result.functions.push(activeFunction);
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
      result.symbols.push(...parameterSymbols(parameters, lineNumber, functionMatch.parameterStart, name));
    }

    if (activeType && depth === activeType.depth && !functionMatch) {
      const fieldMatch = line.match(/^\s*(?:(public|private)\s+)?(?:let\s+(?:this\.)?)?([A-Za-z_]\w*)\s*:\s*([^=\n]+?)(?:\s*=.*)?$/);
      if (fieldMatch && !/^(let|fn|struct|import)\b/.test(trimmed)) {
        const name = fieldMatch[2];
        const column = original.indexOf(name);
        addSymbol(result, {
          name,
          kind: 'field',
          type: fieldMatch[3].trim(),
          visibility: fieldMatch[1] || 'private',
          detail: `${fieldMatch[1] ? `${fieldMatch[1]} ` : ''}${name}: ${fieldMatch[3].trim()}`,
          documentation: precedingDocs(lines, lineNumber),
          line: lineNumber,
          column,
          endColumn: column + name.length,
          container: activeType.name
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
      const letMatch = line.match(/\blet\s+([A-Za-z_]\w*)\s*(?::\s*([^=]+?))?\s*=\s*(.+)$/);
      if (letMatch) {
        const name = letMatch[1];
        const column = original.indexOf(name, original.indexOf('let') + 3);
        const assignment = original.indexOf('=', column + name.length);
        result.symbols.push({
          name, kind: 'variable', type: (letMatch[2] || '').trim(), initializer: assignment >= 0 ? original.slice(assignment + 1).trim() : letMatch[3].trim(),
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
    if (activeFunction && depth < activeFunction.depth) {
      activeFunction.endLine = lineNumber;
      activeFunction = null;
    }
    if (activeType && depth < activeType.depth) activeType = null;
  }
  for (const fn of result.functions) {
    if (fn.endLine === undefined) fn.endLine = lines.length - 1;
  }
  for (let pass = 0; pass < 3; pass += 1) {
    for (const symbol of result.symbols) {
      if (symbol.kind === 'variable' && !symbol.type && symbol.initializer) {
        symbol.type = inferExpressionType(symbol.initializer, result, symbol.line);
        if (symbol.type) symbol.detail = `${symbol.name}: ${symbol.type}`;
      }
    }
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
  const match = before.match(/([A-Za-z_]\w*)(::|\.)([A-Za-z_]\w*)?$/);
  return match ? { qualifier: match[1], separator: match[2], prefix: match[3] || '' } : null;
}

function accessPathAt(line, column) {
  const before = line.slice(0, column);
  const match = before.match(/([A-Za-z_]\w*(?:(?:::|\.)[A-Za-z_]\w*)*)(::|\.)([A-Za-z_]\w*)?$/);
  if (!match) return null;
  return {
    path: match[1],
    qualifier: match[1].split(/::|\./).pop(),
    separator: match[2],
    prefix: match[3] || ''
  };
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

function callPathAt(line, column) {
  const before = line.slice(0, column);
  let depth = 0;
  let comma = 0;
  for (let index = before.length - 1; index >= 0; index -= 1) {
    const char = before[index];
    if (char === ')') depth += 1;
    else if (char === '(') {
      if (depth > 0) depth -= 1;
      else {
        const match = before.slice(0, index).match(/([A-Za-z_]\w*(?:(?:::|\.)[A-Za-z_]\w*)*)\s*$/);
        return match ? { path: match[1], name: match[1].split(/::|\./).pop(), activeParameter: comma } : null;
      }
    } else if (char === ',' && depth === 0) comma += 1;
  }
  return null;
}

function importPathAt(line, column) {
  const before = line.slice(0, column);
  const imported = before.match(/^\s*import\s+(std|crate|[A-Za-z_]\w*)::([A-Za-z_]\w*(?:::[A-Za-z_]\w*)*)?$/);
  if (imported) return { kind: imported[1], prefix: imported[2] || '' };
  return null;
}

function importRootAt(line, column) {
  const match = line.slice(0, column).match(/^\s*import\s+([A-Za-z_]\w*)?$/);
  return match ? { prefix: match[1] || '' } : null;
}

function normalizeType(typeName) {
  let value = String(typeName || '').trim();
  value = value.replace(/\s*\|\s*null\s*$/, '').trim();
  value = value.replace(/^&\s*(?:mut\s+)?/, '').trim();
  return value;
}

function baseType(typeName) {
  const value = normalizeType(typeName);
  let depth = 0;
  for (let index = 0; index < value.length; index += 1) {
    if (value[index] === '<') {
      if (depth === 0) return value.slice(0, index).trim();
      depth += 1;
    } else if (value[index] === '>') depth = Math.max(0, depth - 1);
  }
  return value;
}

function returnTypeFromDetail(detail) {
  const value = String(detail || '').replace(/\s+throws\s+Error\s*$/, '').trim();
  let depth = 0;
  for (let index = 0; index < value.length; index += 1) {
    if (value[index] === '(') depth += 1;
    else if (value[index] === ')') {
      depth -= 1;
      if (depth === 0) return value.slice(index + 1).trim() || 'void';
    }
  }
  return '';
}

function inferExpressionType(expression, parsed, lineNumber = Number.MAX_SAFE_INTEGER) {
  let value = String(expression || '').trim().replace(/\s+catch\b[\s\S]*$/, '').trim();
  if (!value) return '';
  if (/^f?"(?:\\.|[^"])*"$/.test(value)) return 'str';
  if (/^(?:true|false)$/.test(value)) return 'bool';
  if (/^[-+]?\d+[uU]?$/.test(value)) return 'i32';
  if (/^[-+]?(?:\d+\.\d*|\d*\.\d+)(?:[eE][-+]?\d+)?$/.test(value)) return 'f64';
  if (value === 'null') return 'null';
  const cast = value.match(/^\(([^()]+)\)\s*.+$/);
  if (cast) return cast[1].trim();
  const borrow = value.match(/^&\s*(mut\s+)?(.+)$/);
  if (borrow) {
    const targetType = inferExpressionType(borrow[2], parsed, lineNumber);
    return targetType ? `&${borrow[1] ? 'mut ' : ''}${targetType}` : '';
  }
  if (value.startsWith('[') && value.endsWith(']')) {
    const elements = splitTopLevel(value.slice(1, -1));
    if (!elements.length) return 'List';
    const elementTypes = elements.map((item) => inferExpressionType(item, parsed, lineNumber)).filter(Boolean);
    return elementTypes.length && elementTypes.every((item) => item === elementTypes[0]) ? `List<${elementTypes[0]}>` : 'List';
  }
  const indexed = value.match(/^(.+)\[[^\]]+\]$/);
  if (indexed) {
    const receiverType = normalizeType(inferExpressionType(indexed[1], parsed, lineNumber));
    const list = receiverType.match(/^List\s*<(.+)>$/);
    if (list) return list[1].trim();
  }
  const member = value.match(/^([A-Za-z_]\w*)\.([A-Za-z_]\w*)$/);
  if (member) {
    const receiver = [...parsed.symbols].reverse().find(
      (item) => item.name === member[1] && item.line <= lineNumber
    );
    const container = receiver?.type ? baseType(receiver.type).split('::').pop() : '';
    const field = [...parsed.symbols].reverse().find(
      (item) => item.kind === 'field' && item.container === container && item.name === member[2]
    );
    if (field?.type) return field.type;
  }
  const call = value.match(/^((?:[A-Za-z_]\w*(?:::|\.))*[A-Za-z_]\w*)\s*\(/);
  if (call) {
    const name = call[1].split(/::|\./).pop();
    const symbol = parsed.symbols.find((item) => item.name === name && ['function', 'method', 'native'].includes(item.kind));
    if (symbol) return symbol.returnType || returnTypeFromDetail(symbol.detail);
    const memberCall = call[1].match(/^([A-Za-z_]\w*)\.([A-Za-z_]\w*)$/);
    if (memberCall) {
      const receiver = [...parsed.symbols].reverse().find(
        (item) => item.name === memberCall[1] && item.line <= lineNumber
      );
      const receiverType = receiver?.type ? baseType(receiver.type).split('::').pop() : '';
      const member = (BUILTIN_MEMBERS[receiverType] || []).find((item) => item.name === memberCall[2]);
      if (member) return member.returnType || returnTypeFromDetail(member.detail);
    }
    const imported = call[1].split('::');
    if (imported.length === 2) {
      const moduleImport = parsed.imports.find((item) => item.name === imported[0]);
      const moduleName = moduleImport?.path.split('::').pop();
      const builtin = (BUILTINS[moduleName] || []).find(([item]) => item === imported[1]);
      if (builtin) return returnTypeFromDetail(builtin[1]);
    }
  }
  const constructed = value.match(/^((?:[A-Za-z_]\w*::)*[A-Za-z_]\w*(?:<[^>]+>)?)\s*(?:\(|\{)/);
  if (constructed) return constructed[1];
  const local = [...parsed.symbols].reverse().find((item) => item.name === value && item.line <= lineNumber);
  return local?.type || '';
}

module.exports = {
  BUILTINS,
  BUILTIN_MEMBERS,
  BUILTIN_TYPES,
  KEYWORDS,
  SPECIAL_FORMS,
  SPECIAL_VALUES,
  STANDARD_MODULES,
  TYPES,
  accessPathAt,
  baseType,
  callAt,
  callPathAt,
  inferExpressionType,
  importPathAt,
  importRootAt,
  maskLine,
  normalizeType,
  parseDocument,
  qualifierAt,
  returnTypeFromDetail,
  splitTopLevel,
  wordAt
};
