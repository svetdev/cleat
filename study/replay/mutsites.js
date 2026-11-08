// mutsites — the operator sites in TypeScript files that run: binary comparisons, equality,
// && and ||, + and -, a boolean literal outside a type, a prefix !. With --types, the sites in
// type code instead: a conditional type's branches swapped, an optional `?` dropped, one union
// member dropped, `unknown` to `never` (and `never`, `any` to `unknown`), a true/false literal
// type flipped, `readonly` dropped. A conditional whose `extends` infers is not swapped: the
// inferred name binds only in the true branch, so the swap could not compile.
//   node mutsites.js [--types] <path to the typescript package> <root> <file …>   → JSON [[file, start, old, new], …]
const args = process.argv.slice(2);
const types = args[0] === "--types";
if (types) args.shift();
const ts = require(args[0]);
const fs = require("fs");
const path = require("path");
const [root, ...files] = args.slice(1);
const K = ts.SyntaxKind;
const SWAP = {
  [K.LessThanToken]: "<=", [K.LessThanEqualsToken]: "<", [K.GreaterThanToken]: ">=", [K.GreaterThanEqualsToken]: ">",
  [K.EqualsEqualsEqualsToken]: "!==", [K.ExclamationEqualsEqualsToken]: "===",
  [K.AmpersandAmpersandToken]: "||", [K.BarBarToken]: "&&", [K.PlusToken]: "-", [K.MinusToken]: "+",
};
const KEYWORD = { [K.UnknownKeyword]: "never", [K.NeverKeyword]: "unknown", [K.AnyKeyword]: "unknown" };

function runtime(node, src, out, file) {
  if (ts.isBinaryExpression(node) && SWAP[node.operatorToken.kind] !== undefined) {
    const op = node.operatorToken;
    out.push([file, op.getStart(src), op.getText(src), SWAP[op.kind]]);
  } else if (node.kind === K.TrueKeyword || node.kind === K.FalseKeyword) {
    out.push([file, node.getStart(src), node.getText(src), node.kind === K.TrueKeyword ? "false" : "true"]);
  } else if (ts.isPrefixUnaryExpression(node) && node.operator === K.ExclamationToken) {
    out.push([file, node.getStart(src), "!", ""]);
  }
}

function typeLevel(node, src, out, file) {
  const at = (n) => n.getStart(src), text = (n) => n.getText(src);
  const infers = (n) => n.kind === K.InferType || ts.forEachChild(n, infers);
  if (ts.isConditionalTypeNode(node) && !infers(node.extendsType)) {   // an `infer` binds only in the true branch
    const head = text(node.checkType) + " extends " + text(node.extendsType);
    out.push([file, at(node), text(node), head + " ? " + text(node.falseType) + " : " + text(node.trueType)]);
  } else if ((ts.isPropertySignature(node) || ts.isPropertyDeclaration(node)) && node.questionToken) {
    out.push([file, at(node.questionToken), "?", ""]);
  } else if (ts.isUnionTypeNode(node) && node.types.length > 1) {
    node.types.forEach((member, i) => {
      const rest = node.types.filter((_, j) => j !== i).map(text).join(" | ");
      out.push([file, at(node), text(node), rest]);
    });
  } else if (KEYWORD[node.kind] !== undefined && ts.isTypeNode(node)) {
    out.push([file, at(node), text(node), KEYWORD[node.kind]]);
  } else if (ts.isLiteralTypeNode(node) && (node.literal.kind === K.TrueKeyword || node.literal.kind === K.FalseKeyword)) {
    out.push([file, at(node), text(node), node.literal.kind === K.TrueKeyword ? "false" : "true"]);
  } else if (ts.isTypeOperatorNode(node) && node.operator === K.ReadonlyKeyword) {
    out.push([file, at(node), text(node), text(node.type)]);
  }
}

const out = [];
for (const file of files) {
  const src = ts.createSourceFile(file, fs.readFileSync(path.join(root, file), "utf8"), ts.ScriptTarget.Latest, true);
  const visit = (node) => {
    const inType = ts.isTypeNode(node) && !ts.isExpressionWithTypeArguments(node);
    if (!types && inType) return;   // types do not run
    (types ? typeLevel : runtime)(node, src, out, file);
    ts.forEachChild(node, visit);
  };
  visit(src);
}
process.stdout.write(JSON.stringify(out));
