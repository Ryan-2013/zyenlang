'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const language = require('../language');

const source = `import std::io as io

/// A small value type.
public struct Point {
    public x: i32
    public y: i32
}

public class Counter {
    private value: i32 = 0
    label: str = "counter"

    public init(value: i32) {
        this.value = value
    }

    public fn add(amount: i32) i32 {
        return this.value + amount
    }
}

fn pair() (i32, str) {
    return 1, "one"
}

fn main() i32 {
    let counter: Counter = Counter(40)
    let (number, text): (i32, str) = pair()
    io::print(text)
    return counter.add(number)
}`;

const parsed = language.parseDocument(source, 'src/main.zy');

assert.deepStrictEqual(parsed.imports.map((item) => [item.name, item.path]), [['io', 'std::io']]);
assert(parsed.exports.some((item) => item.kind === 'struct' && item.name === 'Point'));
assert(parsed.exports.some((item) => item.kind === 'class' && item.name === 'Counter'));
assert(parsed.exports.some((item) => item.kind === 'field' && item.name === 'value' && item.container === 'Counter' && item.visibility === 'private'));
assert(parsed.exports.some((item) => item.kind === 'field' && item.name === 'label' && item.container === 'Counter' && item.visibility === 'public'));
assert(parsed.exports.some((item) => item.kind === 'constructor' && item.name === 'init' && item.container === 'Counter' && item.visibility === 'public'));
assert(parsed.exports.some((item) => item.kind === 'method' && item.name === 'add' && item.container === 'Counter'));
assert(parsed.exports.some((item) => item.kind === 'function' && item.name === 'pair' && item.returnType === '(i32, str)'));
assert(parsed.symbols.some((item) => item.kind === 'parameter' && item.name === 'amount' && item.type === 'i32'));
assert(parsed.symbols.some((item) => item.kind === 'variable' && item.name === 'counter' && item.type === 'Counter'));
assert(parsed.symbols.some((item) => item.kind === 'variable' && item.name === 'number' && item.type === 'i32'));
assert(parsed.symbols.some((item) => item.kind === 'variable' && item.name === 'text' && item.type === 'str'));

const shortImports = language.parseDocument('import std::io\nimport requests\nimport requests::client as http', 'imports.zy');
assert.deepStrictEqual(shortImports.imports.map((item) => [item.name, item.path]), [
  ['io', 'std::io'],
  ['requests', 'requests'],
  ['http', 'requests::client']
]);

assert.deepStrictEqual(language.wordAt('return counter.add(number)', 16), { value: 'add', start: 15, end: 18 });
assert.deepStrictEqual(language.qualifierAt('    io::pr', 10), { qualifier: 'io', separator: '::', prefix: 'pr' });
assert.deepStrictEqual(language.qualifierAt('    counter.ad', 14), { qualifier: 'counter', separator: '.', prefix: 'ad' });
assert.deepStrictEqual(language.accessPathAt('    tools::Cache::cr', 20), { path: 'tools::Cache', qualifier: 'Cache', separator: '::', prefix: 'cr' });
assert.deepStrictEqual(language.accessPathAt('    counter.va', 14), { path: 'counter', qualifier: 'counter', separator: '.', prefix: 'va' });
assert.deepStrictEqual(language.callAt('    pair(1, other(', 11), { name: 'pair', activeParameter: 1 });
assert.deepStrictEqual(language.callPathAt('    io::print(value, ', 21), { path: 'io::print', name: 'print', activeParameter: 1 });
assert.deepStrictEqual(language.splitTopLevel('List<i32>, fn(i32, str), i32 | null'), ['List<i32>', 'fn(i32, str)', 'i32 | null']);
assert(language.KEYWORDS.includes('TYPEOF__'));
assert(language.KEYWORDS.includes('LIST_LEN__'));
assert(language.KEYWORDS.includes('LIST_SHAPE__'));
assert(language.KEYWORDS.includes('LIST_FILLED__'));
assert(language.KEYWORDS.includes('LIST_PUSH__'));
assert(language.KEYWORDS.includes('LIST_SET__'));
assert(language.KEYWORDS.includes('PRINT_CMD__'));
assert(language.KEYWORDS.includes('STR_TO_LIST__'));
assert(language.KEYWORDS.includes('DROP__'));
assert(language.KEYWORDS.includes('class'));
assert(language.KEYWORDS.includes('defer'));
assert(!language.KEYWORDS.includes('typeof'));
assert.deepStrictEqual(language.SPECIAL_VALUES, []);
assert(language.SPECIAL_FORMS.some((item) => item.name === 'LIST_PUSH__' && item.snippet.includes('${2:value}')));
assert(language.SPECIAL_FORMS.some((item) => item.name === 'LIST_SHAPE__' && item.detail.includes('List<usize> throws Error')));
assert(language.SPECIAL_FORMS.some((item) => item.name === 'LIST_FILLED__' && item.detail.includes('List<T, Rank>')));
assert(language.SPECIAL_FORMS.some((item) => item.name === 'PRINT_CMD__' && item.documentation.includes('#RRGGBB')));
assert(language.SPECIAL_FORMS.some((item) => item.name === 'STR_TO_LIST__' && item.detail.endsWith('List<str>')));
assert(language.SPECIAL_FORMS.some((item) => item.name === 'DROP__' && item.detail === 'DROP__(local) void'));
assert(language.SPECIAL_FORMS.some((item) => item.name === 'CLONE_REF__'));
assert(language.SPECIAL_FORMS.some((item) => item.name === 'REF_SET__'));
assert(language.SPECIAL_FORMS.some((item) => item.name === 'TYPEOF__' && item.detail === 'TYPEOF__(value, Type) bool'));
assert.deepStrictEqual(language.importPathAt('import std::pa', 14), { kind: 'std', prefix: 'pa' });
assert.deepStrictEqual(language.importPathAt('import crate::net::ht', 21), { kind: 'crate', prefix: 'net::ht' });
assert.deepStrictEqual(language.importRootAt('import cr', 9), { prefix: 'cr' });
assert.strictEqual(language.importPathAt('let value = 1', 13), null);
assert(language.BUILTINS.path.some(([name]) => name === 'join'));
assert(language.BUILTINS.fs.some(([name]) => name === 'tree'));
assert(language.BUILTINS.fs.some(([name]) => name === 'write_text'));
assert(language.BUILTINS.fs.some(([name]) => name === 'list_dir'));
assert(language.BUILTINS.time.some(([name]) => name === 'monotonic_milliseconds'));
assert(language.BUILTINS.os.some(([name]) => name === 'current_dir'));
assert(language.BUILTINS.server.some(([name]) => name === 'serve_handler'));
assert(language.BUILTINS.request.some(([name]) => name === 'response_ok'));
assert(!language.BUILTINS.gui.some(([name]) => name === 'button'));
assert(language.BUILTIN_MEMBERS.Application.some((item) => item.name === 'button'));
assert(language.BUILTIN_MEMBERS.Application.some((item) => item.name === 'button_group'));
assert(language.STANDARD_MODULES.includes('c_module'));
assert(language.STANDARD_MODULES.includes('editor'));
assert(language.STANDARD_MODULES.includes('time'));
assert(language.STANDARD_MODULES.includes('os'));
assert(language.BUILTIN_TYPES.c_module.some(([name]) => name === 'Module'));
assert(language.BUILTIN_TYPES.request.some(([name]) => name === 'Response'));

const inferred = language.parseDocument(`import std::gui as gui
fn clicked() void {}
class Cache { public fn open() i32 { return 0 } }
fn main() i32 {
    let count = 10
    let ratio = 1.5
    let names = ["a", "b"]
    let cache = Cache()
    let app = gui::application("Demo", 800, 600)
    let button = app.button(10, 20, 100, 40, "Run", clicked)
    return 0
}`, 'inferred.zy');
assert.strictEqual(inferred.symbols.find((item) => item.name === 'count').type, 'i32');
assert.strictEqual(inferred.symbols.find((item) => item.name === 'ratio').type, 'f64');
assert.strictEqual(inferred.symbols.find((item) => item.name === 'names').type, 'List<str>');
assert.strictEqual(inferred.symbols.find((item) => item.name === 'cache').type, 'Cache');
assert.strictEqual(inferred.symbols.find((item) => item.name === 'app').type, 'Application');
assert.strictEqual(inferred.symbols.find((item) => item.name === 'button').type, 'Button');
assert.strictEqual(language.inferExpressionType('LIST_SHAPE__(names)', inferred, 20), 'List<usize>');
assert.strictEqual(language.inferExpressionType('LIST_FILLED__([2, 3], 0)', inferred, 20), 'List<i32, 2>');
inferred.symbols.push({ name: 'cube', type: 'List<i32, 3>', line: 1 });
assert.strictEqual(language.inferExpressionType('cube[0]', inferred, 20), 'List<i32, 2>');
assert.strictEqual(language.inferExpressionType('cube[0][1][2]', inferred, 20), 'i32');
assert.strictEqual(language.normalizeType('&mut tools::Cache<i32> | null'), 'tools::Cache<i32>');
assert.strictEqual(language.baseType('tools::Cache<i32>'), 'tools::Cache');
assert.strictEqual(language.returnTypeFromDetail('fn get(path: str) Response throws Error'), 'Response');
assert(inferred.functions.some((item) => item.name === 'main'));

const genericClasses = language.parseDocument(`public class Box<T> {
    private value: T
    public init(value: T) { this.value = value }
    public fn get() T { return this.value }
}
fn make_box() Box<i32> { return Box<i32>(10) }
fn main() i32 {
    let box: Box<str> = Box<str>("value")
    let text = box.get()
    let number = make_box().get()
    return 0
}`, 'generic-classes.zy');
const boxDeclaration = genericClasses.exports.find((item) => item.kind === 'class' && item.name === 'Box');
assert.deepStrictEqual(boxDeclaration.typeParameters, ['T']);
assert(genericClasses.exports.some((item) => item.kind === 'constructor' && item.container === 'Box' && item.parameters === 'value: T'));
assert.strictEqual(genericClasses.symbols.find((item) => item.kind === 'variable' && item.name === 'text').type, 'str');
assert.strictEqual(genericClasses.symbols.find((item) => item.kind === 'variable' && item.name === 'number').type, 'i32');
assert.deepStrictEqual(language.parseTypeReference('crate::store::Box<List<i32>>'), {
  raw: 'crate::store::Box<List<i32>>',
  path: 'crate::store::Box',
  name: 'Box',
  namespace: 'crate::store',
  arguments: ['List<i32>']
});
assert.strictEqual(language.substituteType('fn set(value: T) List<T>', { T: 'i32' }), 'fn set(value: i32) List<i32>');
assert.deepStrictEqual(language.memberAccessAt('    let value = make_box().ge', 29), {
  expression: 'make_box()', prefix: 'ge', separator: '.'
});
assert.deepStrictEqual(language.callExpressionAt('    make_box().get(10, ', 23), {
  callee: 'make_box().get', path: 'make_box().get', name: 'get', activeParameter: 1
});

const references = language.parseDocument(`class Bag {
    public items: List<i32> = [1]
}
fn main() i32 {
    let bag = Bag()
    let values: List<i32> = [10]
    let item = &values[0]
    let write = &mut values[0]
    let items = &bag.items
    return 0
}`, 'references.zy');
assert.strictEqual(references.symbols.find((item) => item.name === 'item').type, '&i32');
assert.strictEqual(references.symbols.find((item) => item.name === 'write').type, '&mut i32');
assert.strictEqual(references.symbols.find((item) => item.kind === 'variable' && item.name === 'items').type, '&List<i32>');
assert(language.SPECIAL_FORMS.some((item) => item.name === 'LIST_LEN__' && item.detail.includes('&List<T>')));
assert(language.SPECIAL_FORMS.some((item) => item.name === 'LIST_PUSH__' && item.detail.includes('&mut List<T>')));

const masked = language.parseDocument('fn main() i32 {\n    let value = 1 // value in comment\n    let text = "value in text"\n}', 'masked.zy');
assert(masked.maskedLines[1].includes('let value'));
assert(!masked.maskedLines[1].includes('value in comment'));
assert(!masked.maskedLines[2].includes('value in text'));

const nativeSource = `native source "bridge.c"
private native fn editor_open(path: str) i32 = "zy2_editor_open"`;
const nativeParsed = language.parseDocument(nativeSource, 'native.zy');
assert(nativeParsed.exports.some((item) => item.kind === 'native' && item.name === 'editor_open' && item.returnType === 'i32'));

const nativeModuleParsed = language.parseDocument(`import std::c_module as c
native module graphics = c::load("native/graphics.zlcm.h")
fn main() i32 { return 0 }`, 'native-module.zy');
assert.deepStrictEqual(nativeModuleParsed.nativeModules.map((item) => [item.name, item.loader, item.templatePath]), [
  ['graphics', 'c', 'native/graphics.zlcm.h']
]);
const nativeTemplate = language.parseNativeTemplate(`ZLC_ABI(3)
ZLC_HANDLE(Window, ZLC_DROP(zy_window_drop))
ZLC_ENUM(WindowMode, i32, ZLC_CASE(windowed, 0), ZLC_CASE(fullscreen, 1))
ZLC_FLAGS(WindowFlags, u32, ZLC_CASE(resizable, 4), ZLC_CASE(high_dpi, 8))
ZLC_CONST(DEFAULT_WIDTH, i32, 800)
ZLC_FN(open, zy_window_open, optional<owned<Window>>, ZLC_PARAM(title, str), ZLC_PARAM(width, i32), ZLC_FAIL(null, zy_window_last_error))
ZLC_FN(split, zy_split, void, ZLC_PARAM(value, i32), ZLC_PARAM(left, out<i32>), ZLC_PARAM(right, out<i32>))
// TODO: review void * ownership
`, 'graphics.zlcm.h');
assert.strictEqual(nativeTemplate.abi, 3);
assert(nativeTemplate.symbols.some((item) => item.kind === 'nativeHandle' && item.name === 'Window'));
assert(nativeTemplate.symbols.some((item) => item.kind === 'nativeEnum' && item.name === 'WindowMode'));
assert(nativeTemplate.symbols.some((item) => item.container === 'WindowMode' && item.name === 'fullscreen'));
assert(nativeTemplate.symbols.some((item) => item.kind === 'constant' && item.name === 'DEFAULT_WIDTH'));
assert(nativeTemplate.symbols.some((item) => item.name === 'open' && item.returnType === 'Window | null' && item.throws));
assert(nativeTemplate.symbols.some((item) => item.name === 'split' && item.returnType === '(i32, i32)'));
assert.strictEqual(nativeTemplate.todos.length, 1);

const mutableParsed = language.parseDocument('fn update(mut value: i32) i32 { return value }', 'mut.zy');
assert(mutableParsed.symbols.some((item) => item.kind === 'parameter' && item.name === 'value' && item.detail === 'mut value: i32'));

const defaultParameterParsed = language.parseDocument('fn add<T>(a: i32 = 10, b: T) T { return (T)(a + (i32)b) }', 'defaults.zy');
assert(defaultParameterParsed.exports.some((item) => item.kind === 'function' && item.name === 'add' && item.returnType === 'T'));
assert(defaultParameterParsed.symbols.some((item) => item.kind === 'parameter' && item.name === 'a' && item.type === 'i32'));
assert(defaultParameterParsed.symbols.some((item) => item.kind === 'parameter' && item.name === 'b' && item.type === 'T'));
assert(defaultParameterParsed.exports.some((item) => item.name === 'add' && item.detail.includes('a: i32 = 10')));

const callbackParsed = language.parseDocument(`struct Button {
    public let when_click_func: fn() void
}
fn apply(callback: fn(i32, i32) i32, value: i32) i32 { return callback(value, value) }
fn pick() fn(i32, i32) i32 { return apply }
`, 'callbacks.zy');
assert(callbackParsed.exports.some((item) => item.kind === 'field' && item.name === 'when_click_func' && item.type === 'fn() void'));
assert(callbackParsed.exports.some((item) => item.kind === 'function' && item.name === 'apply' && item.returnType === 'i32'));
assert(callbackParsed.symbols.some((item) => item.kind === 'parameter' && item.name === 'callback' && item.type === 'fn(i32, i32) i32'));
assert(callbackParsed.exports.some((item) => item.kind === 'function' && item.name === 'pick' && item.returnType === 'fn(i32, i32) i32'));

const cExportParsed = language.parseDocument(`#engine_add
fn add(left: i32, right: i32) i32 { return left + right }
`, 'c-export.zy');
assert(cExportParsed.exports.some((item) => item.name === 'add' && item.cExportName === 'engine_add'));
const grammar = fs.readFileSync(path.join(__dirname, '..', 'syntaxes', 'zyen.tmLanguage.json'), 'utf8');
assert(grammar.includes('meta.function.c-export.zyen'));

const crateAlias = language.parseDocument('import crate::tools as tools', 'alias.zy');
assert.strictEqual(crateAlias.imports[0].name, 'tools');

const manifest = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'package.json'), 'utf8'));
assert.strictEqual(manifest.capabilities.untrustedWorkspaces.supported, 'limited');
assert(manifest.capabilities.untrustedWorkspaces.restrictedConfigurations.includes('zyenlang.compilerPath'));
assert(manifest.capabilities.untrustedWorkspaces.restrictedConfigurations.includes('zyenlang.stdlibPath'));
assert(manifest.contributes.configuration.properties['zyenlang.stdlibPath']);
const extensionSource = fs.readFileSync(path.join(__dirname, '..', 'extension.js'), 'utf8');
assert(extensionSource.includes('new vscode.ProcessExecution'));
assert(!extensionSource.includes('.sendText('));
assert(extensionSource.includes('registerDocumentHighlightProvider'));
assert(extensionSource.includes('registerRenameProvider'));
assert(extensionSource.includes('registerTypeDefinitionProvider'));
assert(extensionSource.includes('registerDocumentLinkProvider'));
assert(!extensionSource.includes('registerInlayHintsProvider'));
assert(extensionSource.includes('registerHoverProvider'));
assert(extensionSource.includes('hoverMarkdownFor'));
assert(extensionSource.includes('referencesFor'));
assert(extensionSource.includes("['paths', '--json']"));
assert(extensionSource.includes("['$zyenlang']"));
assert(extensionSource.includes('parsed.maskedLines'));

const snippets = fs.readFileSync(path.join(__dirname, '..', 'snippets', 'zyen.code-snippets'), 'utf8');
assert(snippets.includes('TYPEOF__(${1:value}, ${2:i32})'));
assert(snippets.includes('LIST_SET__(${1:list}, ${2:index}, ${3:value})'));
assert(snippets.includes('PRINT_CMD__(${1:text}'));
assert(snippets.includes('STR_TO_LIST__(${2:text})'));
assert(snippets.includes('DROP__(${1:local})'));
assert(snippets.includes('import std::${1:io}'));

console.log('language tests passed');
